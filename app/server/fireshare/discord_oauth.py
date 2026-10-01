"""Sign-in with Discord, limited to members of one guild (optionally one role).

Only the OAuth2 authorization-code flow against Discord's public API is used: no
bot, no stored tokens. Each login exchanges the code, reads the user and their
membership in the configured guild, then revokes the access token again. That
means membership is only checked when someone signs in, so sessions are capped
(see REVERIFY_SECONDS in auth.py) to force a fresh check periodically.
"""
import re
import unicodedata
from urllib.parse import urlencode

import requests
from flask import current_app

from . import logger
from . import permissions as perms

API_BASE = 'https://discord.com/api/v10'
AUTHORIZE_URL = 'https://discord.com/oauth2/authorize'
SCOPES = 'identify guilds.members.read'
TIMEOUT = 10

_SNOWFLAKE_RE = re.compile(r'^\d{17,20}$')


class DiscordError(Exception):
    """Discord could not be reached or returned something unexpected."""


def is_snowflake(value):
    return isinstance(value, str) and bool(_SNOWFLAKE_RE.match(value))


def credentials_configured():
    cfg = current_app.config
    return bool(cfg.get('DISCORD_CLIENT_ID') and cfg.get('DISCORD_CLIENT_SECRET')
                and cfg.get('DISCORD_REDIRECT_URI'))


def discord_login_settings(config):
    """The active Discord login settings from config.json, or None when it is off."""
    if current_app.config.get('DEMO_MODE') or not credentials_configured():
        return None
    integrations = (config or {}).get('integrations', {})
    if not integrations.get('discord_login_enabled'):
        return None
    guild_id = str(integrations.get('discord_login_guild_id') or '').strip()
    if not is_snowflake(guild_id):
        return None
    role_id = str(integrations.get('discord_login_required_role_id') or '').strip()
    preset = integrations.get('discord_login_default_preset')
    return {
        'guild_id': guild_id,
        'role_id': role_id if is_snowflake(role_id) else None,
        'preset': preset if preset in perms.PRESETS else 'contributor',
        'disable_on_leave': bool(integrations.get('discord_login_disable_on_leave')),
    }


def build_authorize_url(state):
    return AUTHORIZE_URL + '?' + urlencode({
        'response_type': 'code',
        'client_id': current_app.config['DISCORD_CLIENT_ID'],
        'redirect_uri': current_app.config['DISCORD_REDIRECT_URI'],
        'scope': SCOPES,
        'state': state,
        # Skips the consent screen for someone who already authorized the app.
        'prompt': 'none',
    })


def _request(method, path, **kwargs):
    try:
        resp = requests.request(method, API_BASE + path, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise DiscordError(f"request to {path} failed: {e}") from e
    return resp


def exchange_code(code):
    """Trade an authorization code for an access token."""
    cfg = current_app.config
    resp = _request('POST', '/oauth2/token', data={
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': cfg['DISCORD_REDIRECT_URI'],
    }, auth=(cfg['DISCORD_CLIENT_ID'], cfg['DISCORD_CLIENT_SECRET']))
    if resp.status_code != 200:
        raise DiscordError(f"token exchange returned {resp.status_code}")
    token = resp.json().get('access_token')
    if not token:
        raise DiscordError("token exchange returned no access_token")
    return token


def fetch_user(token):
    resp = _request('GET', '/users/@me', headers={'Authorization': f'Bearer {token}'})
    if resp.status_code != 200:
        raise DiscordError(f"/users/@me returned {resp.status_code}")
    data = resp.json()
    if not is_snowflake(str(data.get('id', ''))):
        raise DiscordError("/users/@me returned no usable id")
    return data


def fetch_member(token, guild_id):
    """The user's member object in guild_id, or None if they are not a member."""
    resp = _request('GET', f'/users/@me/guilds/{guild_id}/member',
                    headers={'Authorization': f'Bearer {token}'})
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise DiscordError(f"guild member lookup returned {resp.status_code}")
    return resp.json()


def revoke(token):
    """Best effort: Fireshare never keeps the token, so drop it on Discord's side too."""
    cfg = current_app.config
    try:
        _request('POST', '/oauth2/token/revoke', data={'token': token, 'token_type_hint': 'access_token'},
                 auth=(cfg['DISCORD_CLIENT_ID'], cfg['DISCORD_CLIENT_SECRET']))
    except DiscordError as e:
        logger.debug(f"Discord token revoke failed: {e}")


def member_allowed(member, role_id):
    """Whether a member object grants access: not pending screening, and holding role_id if set."""
    if not member or member.get('pending'):
        return False
    if role_id and role_id not in (member.get('roles') or []):
        return False
    return True


def _slug(name):
    name = unicodedata.normalize('NFKC', name or '')
    name = re.sub(r'[^A-Za-z0-9_.-]+', '-', name)
    name = re.sub(r'[_.-]{2,}', '-', name)
    return re.sub(r'^[^A-Za-z0-9]+|[^A-Za-z0-9]+$', '', name)


def derive_username(discord_username, discord_id, taken):
    """
    A free Fireshare username for a new Discord account.

    `taken(name)` reports whether a name is already in use (case-insensitively).
    Falls back to discord-<last 6 digits of id> when the Discord name is unusable
    or reserved, and appends -2, -3... on collisions.
    """
    base = _slug(discord_username)[:perms.USERNAME_MAX_LENGTH - 4]
    base = re.sub(r'[^A-Za-z0-9]+$', '', base)
    if not perms.normalize_username(base) or base.lower() in perms.RESERVED_USERNAMES:
        base = f"discord-{str(discord_id)[-6:]}"
    candidate = base
    n = 2
    while taken(candidate):
        candidate = f"{base}-{n}"
        n += 1
    return candidate
