"""Connect an app: a device-code flow that ends in an upload token.

Why this exists: an app such as SteamReplay cannot sign in for a user who logs in
with Discord, and asking them to create and paste a token is the step people get
wrong. Instead the app asks for a code, opens /connect?code=… in the browser where
the user is already signed in, and polls until they allow it.

The shape, and why:

* The app holds a long random device code and the user sees a short user code.
  Only the sha256 of the device code is stored, like an upload token, so a copy of
  the database cannot collect anybody's token.
* Approving records who approved. The upload token is minted when the app collects
  it, after re-checking the account, so the raw secret is never stored and exists
  in exactly one response.
* A request lives ten minutes and is collected once. The page names the app and
  shows the code, which is the safeguard against a link sent by somebody else.
"""
import hashlib
import secrets
import threading
import time
from datetime import timedelta

from flask import jsonify, request, Response
from flask_login import current_user

from .. import db, logger
from .. import permissions as P
from ..ip_whitelist import get_client_ip
from ..models import ConnectRequest, User
from . import api
from .decorators import json_body, require_perm
from .upload_tokens import (TOKEN_LIMIT_MESSAGE, _clean_token_name, _now, _throttle_record_failure,
                            _throttle_retry_after, mint_token, token_limit_reached)

REQUEST_LIFETIME = timedelta(minutes=10)
# Seconds between two polls of the app.
POLL_INTERVAL = 2
DEVICE_CODE_BYTES = 32
# No vowels, so a code never spells a word, and no 0/O or 1/I/L look-alikes.
USER_CODE_ALPHABET = 'BCDFGHJKMNPQRSTVWXZ23456789'
USER_CODE_LENGTH = 8

_START_WINDOW_SECONDS = 300
_START_MAX = 20
_start_lock = threading.Lock()
_starts = {}


def _start_retry_after(ip):
    """Seconds before this address may start another request; records the start if 0."""
    now = time.time()
    with _start_lock:
        for key in [k for k, v in _starts.items() if v[-1] <= now - _START_WINDOW_SECONDS]:
            del _starts[key]
        stamps = [t for t in _starts.get(ip, ()) if t > now - _START_WINDOW_SECONDS]
        if len(stamps) >= _START_MAX:
            _starts[ip] = stamps
            return int(stamps[0] + _START_WINDOW_SECONDS - now) + 1
        stamps.append(now)
        _starts[ip] = stamps
        return 0


def _hash(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _normalize_user_code(value):
    if not isinstance(value, str):
        return ''
    return ''.join(ch for ch in value.upper() if ch.isalnum())[:USER_CODE_LENGTH * 2]


def _format_user_code(code):
    half = len(code) // 2
    return f'{code[:half]}-{code[half:]}'


def _new_user_code():
    while True:
        code = ''.join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))
        if not ConnectRequest.query.filter_by(user_code=code).first():
            return code


def _expired(req, now=None):
    return (now or _now()) >= req.expires_at


def _live_request_or_404(user_code):
    req = ConnectRequest.query.filter_by(user_code=_normalize_user_code(user_code)).first()
    if not req or req.status == 'claimed' or _expired(req):
        return None, (jsonify({'error': 'This code is unknown or has expired.'}), 404)
    return req, None


def _request_json(req):
    return {
        'client_name': req.client_name,
        'user_code': _format_user_code(req.user_code),
        'status': req.status,
        'expires_at': req.expires_at.isoformat(),
    }


@api.route('/api/connect/start', methods=['POST'])
@json_body
def connect_start():
    wait = _start_retry_after(get_client_ip())
    if wait:
        return Response(status=429, response='Too many connection attempts. Try again later.',
                        headers={'Retry-After': str(wait)})

    now = _now()
    # Housekeeping: nothing reads a finished or expired request again.
    ConnectRequest.query.filter(
        (ConnectRequest.expires_at <= now) | (ConnectRequest.status == 'claimed')
    ).delete(synchronize_session=False)

    device_code = secrets.token_urlsafe(DEVICE_CODE_BYTES)
    req = ConnectRequest(
        device_code_hash=_hash(device_code),
        user_code=_new_user_code(),
        client_name=_clean_token_name(request.json_body.get('client_name')) or 'App',
        created_at=now,
        expires_at=now + REQUEST_LIFETIME,
        status='pending',
    )
    db.session.add(req)
    db.session.commit()
    user_code = _format_user_code(req.user_code)
    logger.info(f"Connect request {user_code} started for '{req.client_name}' from {get_client_ip()}")
    return jsonify({
        'device_code': device_code,
        'user_code': user_code,
        'verification_path': f'/connect?code={user_code}',
        'expires_in': int(REQUEST_LIFETIME.total_seconds()),
        'interval': POLL_INTERVAL,
    }), 201


@api.route('/api/connect/<user_code>', methods=['GET'])
@require_perm(P.UPLOAD)
def connect_lookup(user_code):
    req, error = _live_request_or_404(user_code)
    if error:
        return error
    return jsonify(_request_json(req))


@api.route('/api/connect/<user_code>/approve', methods=['POST'])
@require_perm(P.UPLOAD)
def connect_approve(user_code):
    req, error = _live_request_or_404(user_code)
    if error:
        return error
    if req.status != 'pending':
        return jsonify({'error': f'This request was already {req.status}.', 'status': req.status}), 409
    if token_limit_reached(current_user):
        return jsonify({'error': TOKEN_LIMIT_MESSAGE}), 400
    req.status = 'approved'
    req.user_id = current_user.id
    db.session.commit()
    logger.info(f"User '{current_user.username}' approved connect request for '{req.client_name}'")
    return jsonify({'status': 'approved'})


@api.route('/api/connect/<user_code>/deny', methods=['POST'])
@require_perm(P.UPLOAD)
def connect_deny(user_code):
    req, error = _live_request_or_404(user_code)
    if error:
        return error
    if req.status != 'pending':
        return jsonify({'error': f'This request was already {req.status}.', 'status': req.status}), 409
    req.status = 'denied'
    req.user_id = current_user.id
    db.session.commit()
    return jsonify({'status': 'denied'})


@api.route('/api/connect/token', methods=['POST'])
@json_body
def connect_collect():
    """The app's poll. Mints the upload token on the first poll after approval."""
    client_ip = get_client_ip()
    wait = _throttle_retry_after(client_ip)
    if wait:
        return Response(status=429, response='Too many invalid codes. Try again later.',
                        headers={'Retry-After': str(wait)})

    device_code = request.json_body.get('device_code')
    if not isinstance(device_code, str) or not device_code or len(device_code) > 128:
        return jsonify({'error': 'device_code is required.'}), 400
    req = ConnectRequest.query.filter_by(device_code_hash=_hash(device_code)).first()
    if not req:
        # Counted like a bad upload token: guessing device codes is guessing tokens.
        _throttle_record_failure(client_ip)
        return jsonify({'status': 'expired'})
    if req.status == 'claimed' or _expired(req):
        return jsonify({'status': 'expired'})
    if req.status != 'approved':
        return jsonify({'status': req.status})

    # Claim before minting, in one conditional update, so two concurrent polls
    # cannot both collect a token.
    claimed = (ConnectRequest.query
               .filter_by(id=req.id, status='approved')
               .update({'status': 'claimed'}, synchronize_session=False))
    db.session.commit()
    if not claimed:
        return jsonify({'status': 'expired'})

    user = db.session.get(User, req.user_id)
    if not user or user.disabled or not user.can(P.UPLOAD):
        return jsonify({'status': 'denied', 'error': 'The account may no longer upload.'})
    if token_limit_reached(user):
        return jsonify({'status': 'denied', 'error': TOKEN_LIMIT_MESSAGE})

    token = mint_token(user, req.client_name)
    return jsonify({'status': 'approved', 'username': user.username, 'token': token})
