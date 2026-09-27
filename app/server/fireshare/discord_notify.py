"""Discord notifications: rich video posts and tag -> person mentions.

Kept out of cli.py so the fork's Discord changes stay isolated from upstream.
"""
import json
import re
from pathlib import Path

import requests
from flask import current_app

from fireshare import logger
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

def build_video_message(video, info, config, domain):
    """Build the webhook payload for a new video post."""
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
    if not password_protected:
        # A bare link lets Discord unfurl the og:video inline player.
        content_lines.append(watch_url)

    return {
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

def send(webhook_url, payload):
    """POST a payload to a Discord webhook. Falls back to no components if Discord rejects them."""
    try:
        if payload.get("components"):
            sep = "&" if "?" in webhook_url else "?"
            response = requests.post(f"{webhook_url}{sep}with_components=true", json=payload, timeout=10)
            if response.status_code == 400:
                logger.warning(f"Discord rejected link button, retrying without it: {response.text[:200]}")
                payload = {k: v for k, v in payload.items() if k != "components"}
                response = requests.post(webhook_url, json=payload, timeout=10)
        else:
            response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        return {"status": "success", "message": "Webhook sent successfully."}
    except requests.exceptions.RequestException as e:
        detail = getattr(getattr(e, "response", None), "text", "") or ""
        logger.error(f"Failed to send Discord webhook: {e} {detail[:200]}")
        return {"status": "error", "message": str(e)}


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
    payload = build_video_message(video, info, config, domain)
    if not payload:
        logger.warning("Unable to post to Discord: set the DOMAIN env variable or a shareable link domain in Settings.")
        return None
    logger.info(f"Posting to Discord webhook for {video_id}")
    result = send(webhook_url, payload)
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
    payload = build_video_message(video, video.info, config, domain) if video else None
    if not payload:
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
    else:
        payload["content"] = "🧪 **Test message** — this is how new clips will look.\n" + payload["content"]
    return send(webhook_url, payload)
