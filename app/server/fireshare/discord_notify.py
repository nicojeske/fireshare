"""Discord notifications: rich video posts and tag -> person mentions.

Kept out of cli.py so the fork's Discord changes stay isolated from upstream.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

import requests
from flask import current_app

from fireshare import logger, util
from fireshare.models import Video, VideoInfo, VideoTagLink, VideoGameLink, CustomTag

AVATAR_URL = "https://github.com/fireshare-app/fireshare/raw/develop/app/client/src/assets/logo_square.png"
DEFAULT_COLOR = 0xF26A21  # Fireshare orange
DISCORD_ID_RE = re.compile(r"^\d{17,20}$")


# --- "already posted" bookkeeping -------------------------------------------

def _posted_file():
    data_dir = Path(current_app.config.get('DATA_DIRECTORY', '/data'))
    return data_dir / 'discord_posted.json'

def _load_posted():
    posted_file = _posted_file()
    if posted_file.exists():
        try:
            with open(posted_file, 'r') as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError):
            return set()
    return set()

def _mark_posted(video_id):
    posted = _load_posted()
    posted.add(video_id)
    posted_file = _posted_file()
    posted_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(posted_file, 'w') as f:
            json.dump(sorted(posted), f)
    except IOError as e:
        logger.error(f"Could not save Discord posted list: {e}")


# --- pending queue: posts wait until transcoding is done ----------------------

def _pending_file():
    return _posted_file().with_name('discord_pending.json')

def _load_pending():
    pending_file = _pending_file()
    if pending_file.exists():
        try:
            with open(pending_file, 'r') as f:
                return list(json.load(f))
        except (json.JSONDecodeError, IOError):
            return []
    return []

def _save_pending(pending):
    pending_file = _pending_file()
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(pending_file, 'w') as f:
            json.dump(pending, f)
    except IOError as e:
        logger.error(f"Could not save Discord pending list: {e}")

def queue(video_id):
    """Mark a video to be posted at the next flush (after transcoding)."""
    pending = _load_pending()
    if video_id not in pending and video_id not in _load_posted():
        pending.append(video_id)
        _save_pending(pending)

def _acquire_flush_lock():
    lock = _posted_file().with_name('discord_flush.lock')
    try:
        # A lock older than 30 minutes belongs to a crashed process
        if lock.exists() and time.time() - lock.stat().st_mtime > 1800:
            lock.unlink()
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return lock
    except FileExistsError:
        return None
    except OSError as e:
        logger.error(f"Could not create Discord flush lock: {e}")
        return None

def flush_pending():
    """Post every queued video. Only one process flushes at a time."""
    # Re-check after releasing the lock: a video queued while another process was
    # finishing its flush would otherwise wait for the next flush.
    for _ in range(3):
        if not _load_pending():
            return
        if not _flush_once():
            return

def _flush_once():
    lock = _acquire_flush_lock()
    if not lock:
        logger.debug("Another process is posting to Discord; leaving the queue to it")
        return False
    try:
        while True:
            pending = _load_pending()
            if not pending:
                break
            video_id = pending.pop(0)
            # Removed before sending so a crash can't cause a double post
            _save_pending(pending)
            try:
                notify_new_video(video_id)
            except Exception as e:
                logger.error(f"Discord post failed for {video_id}: {e}")
    finally:
        try:
            lock.unlink()
        except OSError:
            pass
    return True

def queue_and_flush_async(video_id):
    """Queue a video and post it from a background thread (for HTTP requests)."""
    queue(video_id)
    app = current_app._get_current_object()
    def run():
        with app.app_context():
            try:
                flush_pending()
            except Exception as e:
                logger.error(f"Discord background post failed: {e}")
    threading.Thread(target=run, daemon=True).start()


# --- preview clip ---------------------------------------------------------------

def _preview_source(video):
    processed = Path(current_app.config['PROCESSED_DIRECTORY'])
    cropped = processed / "derived" / video.video_id / f"{video.video_id}-cropped.mp4"
    if cropped.exists():
        return cropped
    return processed / "video_links" / f"{video.video_id}{video.extension}"

def ensure_preview(video, config, allow_encode=True):
    """Create (or reuse) the Discord preview clip. Returns (path, fits_limit) or (None, False)."""
    limit_mb = int(config.get("integrations", {}).get("discord_upload_limit_mb", 10) or 10)
    source = _preview_source(video)
    if not source.exists():
        return (None, False)
    out = Path(current_app.config['PROCESSED_DIRECTORY']) / "derived" / video.video_id / f"{video.video_id}-discord.mp4"
    if out.exists() and out.stat().st_size > 0 and out.stat().st_mtime >= source.stat().st_mtime:
        return (out, out.stat().st_size <= limit_mb * 1000 * 1000)
    if not allow_encode:
        return (None, False)
    out.parent.mkdir(parents=True, exist_ok=True)
    ok, fits = util.create_discord_preview(source, out, limit_mb)
    return (out, fits) if ok else (None, False)


# --- helpers ------------------------------------------------------------------

def _load_config():
    paths = current_app.config['PATHS']
    with open(paths["data"] / "config.json") as f:
        return json.load(f)

def _base_url(config, domain):
    base = config.get("ui_config", {}).get("shareable_link_domain", "") or domain or ""
    if not base:
        return None
    if not base.startswith("https://") and not base.startswith("http://"):
        base = f"https://{base}"
    return base.rstrip("/")

def mentions_for_tags(tag_ids, config):
    """Return (user_ids, role_ids) mapped to the given tag ids."""
    mapping = config.get("integrations", {}).get("discord_tag_mentions", {}) or {}
    users, roles = [], []
    for tid in tag_ids:
        entry = mapping.get(str(tid))
        if not entry:
            continue
        discord_id = str(entry.get("id", "")).strip()
        if not DISCORD_ID_RE.match(discord_id):
            continue
        target = roles if entry.get("type") == "role" else users
        if discord_id not in target:
            target.append(discord_id)
    return users, roles

def _mention_text(users, roles):
    return " ".join([f"<@{u}>" for u in users] + [f"<@&{r}>" for r in roles])

def _format_duration(seconds):
    if not seconds:
        return None
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _parse_color(hex_color):
    if hex_color and re.match(r"^#[0-9a-fA-F]{6}$", hex_color):
        return int(hex_color[1:], 16)
    return None


# --- message building ---------------------------------------------------------

def _direct_video_url(video, info, base, preview_path):
    """The H.264 file Discord should play when the clip isn't attached."""
    derived = f"{base}/_content/derived/{video.video_id}"
    if preview_path:
        return f"{derived}/{preview_path.name}"
    for height in (1080, 720):
        if getattr(info, f"has_{height}p", False):
            return f"{derived}/{video.video_id}-{height}p.mp4"
    return None

def build_video_message(video, info, config, domain, preview_path=None, attach=False):
    """Build the webhook payload for a new video post.

    attach=True: the preview clip is uploaded with the message, so no link is needed.
    Otherwise content links straight to an H.264 file for Discord's inline player.
    The embed title and Watch button always open the watch page (the original)."""
    base = _base_url(config, domain)
    if not base:
        return None
    watch_url = f"{base}/w/{video.video_id}"

    tags = (CustomTag.query.join(VideoTagLink, VideoTagLink.tag_id == CustomTag.id)
            .filter(VideoTagLink.video_id == video.video_id).order_by(CustomTag.name).all())
    game_link = VideoGameLink.query.filter_by(video_id=video.video_id).first()
    game = game_link.game if game_link else None
    users, roles = mentions_for_tags([t.id for t in tags], config)
    password_protected = bool(info.password_hash)

    fields = []
    if game:
        fields.append({"name": "🎮 Game", "value": game.name, "inline": True})
    duration = _format_duration(info.duration)
    if duration:
        fields.append({"name": "⏱ Length", "value": duration, "inline": True})
    if info.height:
        fields.append({"name": "📺 Quality", "value": f"{info.height}p", "inline": True})
    if tags:
        fields.append({"name": "🏷 Tags", "value": " · ".join(t.name for t in tags)[:1024], "inline": False})
    if video.uploader:
        fields.append({"name": "👤 Uploaded by", "value": video.uploader.name, "inline": True})

    embed = {
        "title": (info.title or "New clip")[:256],
        "url": watch_url,
        "color": next((c for c in (_parse_color(t.color) for t in tags) if c is not None), DEFAULT_COLOR),
        "fields": fields,
        "footer": {"text": "Fireshare"},
    }
    if info.description:
        desc = info.description.strip()
        embed["description"] = desc if len(desc) <= 200 else desc[:197] + "…"
    if video.created_at:
        embed["timestamp"] = video.created_at.isoformat()
    if game and game.steamgriddb_id:
        embed["thumbnail"] = {"url": f"{base}/api/game/assets/{game.steamgriddb_id}/icon_1.webp"}
    if password_protected:
        # The inline player can't play a password-protected video, so show the poster instead.
        embed["image"] = {"url": f"{base}/api/video/poster?id={video.video_id}"}

    content_lines = []
    mention_line = _mention_text(users, roles)
    if mention_line:
        content_lines.append(mention_line)
    if not password_protected and not attach:
        # A direct link to an H.264 file makes Discord show its inline player;
        # the original (often HEVC) usually doesn't play there.
        content_lines.append(_direct_video_url(video, info, base, preview_path) or watch_url)

    payload = {
        "username": "Fireshare",
        "avatar_url": AVATAR_URL,
        "content": "\n".join(content_lines),
        "embeds": [embed],
        "components": [{
            "type": 1,
            "components": [{"type": 2, "style": 5, "label": "Watch on Fireshare", "emoji": {"name": "▶️"}, "url": watch_url}],
        }],
        # Never let titles/descriptions ping @everyone or arbitrary users.
        "allowed_mentions": {"parse": [], "users": users, "roles": roles},
    }
    if attach and preview_path:
        payload["attachments"] = [{"id": 0, "filename": f"{_safe_filename(info.title)}.mp4"}]
    return payload

def _safe_filename(title):
    name = re.sub(r"[^\w\- ]+", "", title or "").strip().replace(" ", "_")
    return (name or "clip")[:80]

def build_tag_ping_message(video, info, tag_ids, config, domain):
    base = _base_url(config, domain)
    users, roles = mentions_for_tags(tag_ids, config)
    if not base or not (users or roles):
        return None
    watch_url = f"{base}/w/{video.video_id}"
    title = (info.title or "a clip").replace("*", "\\*")
    verb = "were" if len(users) + len(roles) > 1 else "was"
    return {
        "username": "Fireshare",
        "avatar_url": AVATAR_URL,
        # Angle brackets suppress a second preview of the same video.
        "content": f"🔔 {_mention_text(users, roles)} {verb} tagged in **{title}** — <{watch_url}>",
        "allowed_mentions": {"parse": [], "users": users, "roles": roles},
    }


# --- sending ------------------------------------------------------------------

def _post(webhook_url, payload, file_path=None):
    url = webhook_url
    if payload.get("components"):
        url += ("&" if "?" in url else "?") + "with_components=true"
    if not file_path:
        return requests.post(url, json=payload, timeout=10)
    filename = payload["attachments"][0]["filename"]
    with open(file_path, "rb") as fh:
        return requests.post(
            url,
            data={"payload_json": json.dumps(payload)},
            files={"files[0]": (filename, fh, "video/mp4")},
            timeout=120,
        )

def _too_large(response):
    if response.status_code == 413:
        return True
    try:
        return response.json().get("code") == 40005
    except ValueError:
        return False

def send(webhook_url, payload, file_path=None, fallback_payload=None):
    """POST to a Discord webhook.

    Retries without the link button if Discord rejects components, and without the
    attachment (using fallback_payload) if the file is over the server's upload limit."""
    try:
        response = _post(webhook_url, payload, file_path)
        if file_path and fallback_payload and _too_large(response):
            logger.warning("Discord preview is over the upload limit, posting a link instead")
            payload, file_path = fallback_payload, None
            response = _post(webhook_url, payload)
        if response.status_code == 400 and payload.get("components"):
            logger.warning(f"Discord rejected link button, retrying without it: {response.text[:200]}")
            payload = {k: v for k, v in payload.items() if k != "components"}
            response = _post(webhook_url, payload, file_path)
        response.raise_for_status()
        return {"status": "success", "message": "Webhook sent successfully."}
    except requests.exceptions.RequestException as e:
        detail = getattr(getattr(e, "response", None), "text", "") or ""
        logger.error(f"Failed to send Discord webhook: {e} {detail[:200]}")
        return {"status": "error", "message": str(e)}


def _send_video(webhook_url, video, info, config, domain, prefix="", allow_encode=True):
    """Build and send a video post, attaching the preview clip when it fits."""
    preview_path, fits = (None, False)
    if not info.password_hash:
        preview_path, fits = ensure_preview(video, config, allow_encode)
    attach = bool(preview_path and fits and config.get("integrations", {}).get("discord_attach_preview", True))
    linked = build_video_message(video, info, config, domain, preview_path, attach=False)
    if not linked:
        return None
    if prefix:
        linked["content"] = prefix + linked["content"]
    if not attach:
        return send(webhook_url, linked)
    attached = build_video_message(video, info, config, domain, preview_path, attach=True)
    if prefix:
        attached["content"] = prefix + attached["content"]
    return send(webhook_url, attached, file_path=preview_path, fallback_payload=linked)


def notify_new_video(video_id, config=None, domain=None):
    """Post a public video to Discord once. Returns the send result or None if skipped."""
    config = config or _load_config()
    domain = domain if domain is not None else current_app.config.get('DOMAIN')
    webhook_url = config.get("integrations", {}).get("discord_webhook_url")
    if not webhook_url:
        return None
    info = VideoInfo.query.filter_by(video_id=video_id).first()
    video = Video.query.filter_by(video_id=video_id).first()
    if not video or not info:
        return None
    if info.private:
        logger.info(f"Skipping Discord post for {video_id}: video is private")
        return None
    if video_id in _load_posted():
        return None
    logger.info(f"Posting to Discord webhook for {video_id}")
    result = _send_video(webhook_url, video, info, config, domain)
    if result is None:
        logger.warning("Unable to post to Discord: set the DOMAIN env variable or a shareable link domain in Settings.")
        return None
    if result["status"] == "success":
        _mark_posted(video_id)
    return result


def notify_tag_added(video_id, tag_ids, config=None, domain=None):
    """Ping the people mapped to newly added tags on an already-posted video."""
    if not tag_ids:
        return None
    config = config or _load_config()
    domain = domain if domain is not None else current_app.config.get('DOMAIN')
    integrations = config.get("integrations", {})
    webhook_url = integrations.get("discord_webhook_url")
    if not webhook_url or not integrations.get("discord_ping_on_tag_add", True):
        return None
    if video_id not in _load_posted():
        return None
    info = VideoInfo.query.filter_by(video_id=video_id).first()
    video = Video.query.filter_by(video_id=video_id).first()
    if not video or not info or info.private:
        return None
    payload = build_tag_ping_message(video, info, tag_ids, config, domain)
    if not payload:
        return None
    logger.info(f"Pinging Discord for new tags {tag_ids} on {video_id}")
    return send(webhook_url, payload)


def send_test_message(webhook_url, config=None, domain=None):
    """Send a sample rich message built from the newest public video (or a placeholder)."""
    config = config or _load_config()
    domain = domain if domain is not None else current_app.config.get('DOMAIN')
    video = (Video.query.join(VideoInfo).filter(VideoInfo.private.is_(False))
             .order_by(Video.created_at.desc()).first())
    if video:
        result = _send_video(webhook_url, video, video.info, config, domain,
                             prefix="🧪 **Test message** — this is how new clips will look.\n",
                             # Encoding runs inside the HTTP request, so keep it short
                             allow_encode=(video.info.duration or 0) <= 90)
        if result is not None:
            return result
    payload = {
        "username": "Fireshare",
        "avatar_url": AVATAR_URL,
        "content": "",
        "embeds": [{
            "title": "Fireshare test message",
            "description": "Discord notifications are working. New public videos will be posted here.",
            "color": DEFAULT_COLOR,
            "footer": {"text": "Fireshare"},
        }],
        "allowed_mentions": {"parse": []},
    }
    return send(webhook_url, payload)
