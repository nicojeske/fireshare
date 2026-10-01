import base64
import io
import json
import secrets
import time

import pyotp
import qrcode
from flask import Blueprint, redirect, request, Response, jsonify, current_app, session
from flask_login import login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from .models import User
from . import db
from . import permissions as fs_permissions
from .api.misc import _get_local_version, _fetch_release_notes
from .api.decorators import demo_restrict
from .ip_whitelist import login_ip_required, get_client_ip, is_ip_permitted
from . import login_throttle
from . import discord_oauth
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone

auth = Blueprint('auth', __name__)

MFA_PENDING_MAX_AGE = 300
MFA_MAX_ATTEMPTS = 5

# Discord membership is only checked at sign-in, so a session of a Discord-only
# account is dropped after this long to force a fresh check. With prompt=none the
# re-login is a single click for someone still in the server.
DISCORD_REVERIFY_SECONDS = 24 * 3600
DISCORD_STATE_MAX_AGE = 600
# Throttle key for failed Discord sign-ins. Not a valid username, so it can never
# share a counter with a password account.
DISCORD_THROTTLE_KEY = '@discord'

def _clear_mfa_pending():
    session.pop('mfa_pending_user_id', None)
    session.pop('mfa_pending_at', None)
    session.pop('mfa_attempts', None)
    session.pop('mfa_username', None)
    session.pop('mfa_via_discord', None)

def _verify_totp(user, code):
    """
    Return the 30s timestep the code matches (with one step of clock drift
    tolerance either way), or None. Each code is only accepted once (RFC 6238):
    a code at or before the last accepted timestep is rejected as a replay.
    """
    code = str(code or '').strip()
    if not code:
        return None
    totp = pyotp.TOTP(user.totp_secret)
    now_step = int(time.time() // 30)
    for offset in (0, -1, 1):
        step = now_step + offset
        if pyotp.utils.strings_equal(totp.at(step * 30), code):
            if user.totp_last_used is not None and step <= user.totp_last_used:
                return None
            return step
    return None

def _record_login(user):
    user.last_login_at = datetime.utcnow()
    db.session.commit()


@auth.route('/api/login', methods=['POST'])
@login_ip_required
def login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return Response(response="Invalid request", status=400)
    username = body.get('username')
    password = body.get('password')
    if not isinstance(username, str) or not isinstance(password, str):
        return Response(response="Invalid username or password", status=401)

    client_ip = get_client_ip()
    wait = login_throttle.retry_after(client_ip, username)
    if wait:
        # The hash comparison below is deliberately skipped while throttled, so a
        # blocked caller cannot keep the server doing pbkdf2 work for them.
        return Response(
            response=f"Too many failed sign-in attempts. Try again in {wait} seconds.",
            status=429,
            headers={'Retry-After': str(wait)},
        )

    user = User.query.filter_by(username=username).first()

    # A user awaiting an invite has no password hash, and a disabled account must
    # not authenticate at all. Both are reported as ordinary credential failures
    # so the response never distinguishes them from a wrong password.
    if user and user.password and not user.disabled and check_password_hash(user.password, password):
        if user.mfa_enabled and user.totp_secret:
            session['mfa_pending_user_id'] = user.id
            session['mfa_pending_at'] = time.time()
            session['mfa_attempts'] = 0
            session['mfa_username'] = user.username
            session.pop('mfa_via_discord', None)
            return jsonify({'mfa_required': True})
        _clear_mfa_pending()
        login_throttle.clear(client_ip, username)
        login_user(user, remember=True)
        _record_login(user)
        return Response(status=200)

    login_throttle.record_failure(client_ip, username)
    return Response(response="Invalid username or password", status=401)

@auth.route('/api/login/mfa', methods=['POST'])
@login_ip_required
def login_mfa():
    pending_user_id = session.get('mfa_pending_user_id')
    pending_at = session.get('mfa_pending_at', 0)
    attempts = session.get('mfa_attempts', 0)

    expired = time.time() - pending_at > MFA_PENDING_MAX_AGE
    if not pending_user_id or expired or attempts >= MFA_MAX_ATTEMPTS:
        _clear_mfa_pending()
        return jsonify({'error': 'Login session expired. Please sign in again.', 'restart': True}), 401

    # The per-session cap above resets whenever the password step is passed again,
    # so on its own it only ever costs an attacker who already holds the password a
    # fresh login round per five guesses. Failed codes are therefore counted against
    # the same address/account budget as failed passwords.
    client_ip = get_client_ip()
    pending_username = session.get('mfa_username')
    wait = login_throttle.retry_after(client_ip, pending_username)
    if wait:
        return jsonify({'error': f'Too many failed attempts. Try again in {wait} seconds.'}), 429

    user = db.session.get(User, pending_user_id)
    if not user or user.disabled or not user.mfa_enabled or not user.totp_secret:
        _clear_mfa_pending()
        return jsonify({'error': 'Login session expired. Please sign in again.', 'restart': True}), 401

    session['mfa_attempts'] = attempts + 1

    matched_step = _verify_totp(user, (request.json or {}).get('code'))
    if matched_step is None:
        login_throttle.record_failure(client_ip, pending_username)
        return jsonify({'error': 'Invalid authentication code.'}), 401

    user.totp_last_used = matched_step
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    login_throttle.clear(client_ip, pending_username)
    via_discord = bool(session.get('mfa_via_discord'))
    _clear_mfa_pending()
    # A remember-me cookie would outlive the periodic Discord membership re-check.
    login_user(user, remember=not via_discord)
    if via_discord:
        session['discord_verified_at'] = time.time()
    return jsonify({'authenticated': True})

@auth.route('/api/mfa/status', methods=['GET'])
@login_required
def mfa_status():
    is_demo = current_app.config.get('DEMO_MODE') and current_user.username == 'demo'
    return jsonify({
        'enabled': bool(current_user.mfa_enabled),
        'supported': not is_demo,
    })

@auth.route('/api/mfa/setup', methods=['POST'])
@login_required
@demo_restrict
def mfa_setup():
    if current_user.mfa_enabled:
        return jsonify({'error': 'Two-factor authentication is already enabled.'}), 400

    secret = pyotp.random_base32()
    current_user.totp_secret = secret
    db.session.commit()

    otpauth_url = pyotp.totp.TOTP(secret).provisioning_uri(name=current_user.username, issuer_name='Fireshare')
    png = io.BytesIO()
    qrcode.make(otpauth_url).save(png, format='PNG')
    qr_data_uri = 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode('ascii')

    return jsonify({'secret': secret, 'otpauth_url': otpauth_url, 'qr': qr_data_uri})

@auth.route('/api/mfa/confirm', methods=['POST'])
@login_required
@demo_restrict
def mfa_confirm():
    if current_user.mfa_enabled:
        return jsonify({'error': 'Two-factor authentication is already enabled.'}), 400
    if not current_user.totp_secret:
        return jsonify({'error': 'Two-factor authentication setup has not been started.'}), 400

    matched_step = _verify_totp(current_user, (request.json or {}).get('code'))
    if matched_step is None:
        return jsonify({'error': 'Invalid authentication code.'}), 400

    current_user.mfa_enabled = True
    current_user.totp_last_used = matched_step
    db.session.commit()
    return jsonify({'enabled': True})

@auth.route('/api/mfa/disable', methods=['POST'])
@login_required
@demo_restrict
def mfa_disable():
    if not current_user.mfa_enabled or not current_user.totp_secret:
        return jsonify({'error': 'Two-factor authentication is not enabled.'}), 400

    # A valid, unused current code is required so a hijacked session (or a
    # just-observed code) alone cannot strip MFA.
    if _verify_totp(current_user, (request.json or {}).get('code')) is None:
        return jsonify({'error': 'Invalid authentication code.'}), 400

    current_user.totp_secret = None
    current_user.mfa_enabled = False
    current_user.totp_last_used = None
    db.session.commit()
    return jsonify({'enabled': False})

# /api/signup was removed in favour of POST /api/admin/users. It was gated only by
# @login_required while User.admin defaulted to True, so any signed-in non-admin
# could create an administrator account.

@auth.route('/api/loggedin', methods=['GET'])
def loggedin():
    login_allowed = is_ip_permitted(get_client_ip())
    if not current_user.is_authenticated:
        return jsonify({'authenticated': False, 'login_allowed': login_allowed})

    release_data = _fetch_release_notes()
    local_version = _get_local_version()

    latest_release = None
    if release_data and local_version:
        latest_version = release_data['version']
        update_available = tuple(int(x) for x in latest_version.split('.')) > tuple(int(x) for x in local_version.split('.'))
        if update_available:
            current_app.logger.info(f"A new version of Fireshare is available! You have v{local_version}, latest is v{latest_version}.")
            is_dev = current_app.config.get('ENVIRONMENT') == 'dev'
            release_is_old_enough = is_dev
            if not is_dev:
                try:
                    published_dt = datetime.fromisoformat(release_data.get('published_at', '').replace('Z', '+00:00'))
                    release_is_old_enough = (datetime.now(timezone.utc) - published_dt).total_seconds() >= 86400
                except (ValueError, TypeError):
                    pass
            if release_is_old_enough:
                latest_release = release_data
        else:
            pass

    return jsonify({
        'authenticated': True,
        'admin': current_user.admin,
        'username': current_user.username,
        'display_name': current_user.display_name,
        'name': current_user.name,
        'avatar_url': current_user.avatar_url(),
        # Admins bypass every check, so the client is handed the full grantable set
        # rather than an empty list it would have to special-case.
        'permissions': (list(fs_permissions.GRANTABLE_PERMISSIONS) if current_user.admin
                        else sorted(current_user.granted_permissions)),
        'must_change_password': bool(current_user.must_change_password),
        'has_password': bool(current_user.password),
        'discord_linked': bool(current_user.discord_id),
        'latest_release': latest_release,
        'login_allowed': login_allowed,
    })

@auth.route('/api/logout', methods=['POST'])
def logout():
    _clear_mfa_pending()
    session.pop('discord_verified_at', None)
    logout_user()
    return Response(status=200)


def _read_config():
    config_path = current_app.config['PATHS']['data'] / 'config.json'
    try:
        with open(config_path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _discord_fail(code, client_ip=None):
    """Redirect back to the login page with an error code the form translates."""
    if client_ip is not None:
        login_throttle.record_failure(client_ip, DISCORD_THROTTLE_KEY)
    return redirect(f'/login?discord_error={code}', code=302)


def _create_discord_user(discord_user, preset):
    def taken(name):
        return User.query.filter(db.func.lower(User.username) == name.lower()).first() is not None

    for attempt in range(2):
        username = discord_oauth.derive_username(discord_user.get('username'), discord_user['id'], taken)
        user = User(
            username=username,
            discord_id=str(discord_user['id']),
            password=None,
            admin=False,
            permissions=fs_permissions.serialize_permissions(fs_permissions.PRESETS[preset]),
            display_name=fs_permissions.clean_display_name(discord_user.get('global_name')),
            profile_public=True,
            created_at=datetime.utcnow(),
            avatar_version=0,
            disabled=False,
        )
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # Two first logins racing for the same username (or the same Discord id).
            db.session.rollback()
            existing = User.query.filter_by(discord_id=str(discord_user['id'])).first()
            if existing:
                return existing
            if attempt:
                raise
            continue
        current_app.logger.info(
            f"Created user '{user.username}' from Discord account {user.discord_id} (preset {preset})"
        )
        return user


@auth.route('/api/auth/discord/start', methods=['GET'])
@login_ip_required
def discord_start():
    if not discord_oauth.discord_login_settings(_read_config()):
        return Response(status=404)
    if login_throttle.retry_after(get_client_ip(), DISCORD_THROTTLE_KEY):
        return _discord_fail('throttled')
    state = secrets.token_urlsafe(32)
    session['discord_oauth_state'] = state
    session['discord_oauth_at'] = time.time()
    return redirect(discord_oauth.build_authorize_url(state), code=302)


@auth.route('/api/auth/discord/callback', methods=['GET'])
@login_ip_required
def discord_callback():
    settings = discord_oauth.discord_login_settings(_read_config())
    if not settings:
        return Response(status=404)
    client_ip = get_client_ip()
    if login_throttle.retry_after(client_ip, DISCORD_THROTTLE_KEY):
        return _discord_fail('throttled')

    expected_state = session.pop('discord_oauth_state', None)
    started_at = session.pop('discord_oauth_at', 0)
    state = request.args.get('state', '')
    if (not expected_state or not secrets.compare_digest(expected_state, state)
            or time.time() - started_at > DISCORD_STATE_MAX_AGE):
        return _discord_fail('state', client_ip)

    code = request.args.get('code')
    if request.args.get('error') or not code:
        return _discord_fail('denied')

    try:
        token = discord_oauth.exchange_code(code)
        try:
            discord_user = discord_oauth.fetch_user(token)
            member = discord_oauth.fetch_member(token, settings['guild_id'])
        finally:
            discord_oauth.revoke(token)
    except discord_oauth.DiscordError as e:
        current_app.logger.warning(f"Discord login failed: {e}")
        return _discord_fail('unavailable')

    discord_id = str(discord_user['id'])
    # Matched only on the Discord id: a Discord name equal to a local username must
    # never open that local account.
    user = User.query.filter_by(discord_id=discord_id).first()

    if not discord_oauth.member_allowed(member, settings['role_id']):
        if user and settings['disable_on_leave'] and not user.disabled and not user.admin:
            user.disabled = True
            db.session.commit()
            current_app.logger.info(
                f"Disabled user '{user.username}': no longer an eligible member of the Discord server"
            )
        reason = 'missing_role' if member and not member.get('pending') else 'not_member'
        return _discord_fail(reason, client_ip)

    if user is None:
        user = _create_discord_user(discord_user, settings['preset'])
    if user.disabled:
        return _discord_fail('disabled', client_ip)

    login_throttle.clear(client_ip, DISCORD_THROTTLE_KEY)
    if user.mfa_enabled and user.totp_secret:
        session['mfa_pending_user_id'] = user.id
        session['mfa_pending_at'] = time.time()
        session['mfa_attempts'] = 0
        session['mfa_username'] = user.username
        session['mfa_via_discord'] = True
        return redirect('/login?mfa=1', code=302)

    _clear_mfa_pending()
    # No remember-me cookie: it would outlive the periodic membership re-check.
    login_user(user, remember=False)
    session['discord_verified_at'] = time.time()
    _record_login(user)
    return redirect('/', code=302)


@auth.before_app_request
def _expire_unverified_discord_sessions():
    """Log out Discord-only accounts whose membership was last checked too long ago."""
    if not current_user.is_authenticated:
        return
    if not current_user.discord_id or current_user.password:
        return
    verified_at = session.get('discord_verified_at')
    if not verified_at or time.time() - verified_at > DISCORD_REVERIFY_SECONDS:
        session.pop('discord_verified_at', None)
        logout_user()
