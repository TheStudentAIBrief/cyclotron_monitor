import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Response
from fastapi.security import OAuth2PasswordBearer

from api import users
from api.config import get_config

# Never ship a hardcoded signing key. Production sets API_SECRET_KEY (Render does this
# via generateValue). If it is absent, fail *closed* to safety: generate a strong
# ephemeral per-process key so the server still runs for dev/CI, but it never trusts a
# publicly-known default — so forged tokens are impossible regardless of configuration.
_SECRET = os.environ.get('API_SECRET_KEY')
if not _SECRET:
    logging.getLogger('uvicorn.error').warning(
        'API_SECRET_KEY is not set — using an ephemeral per-process signing key. '
        'Tokens will not survive a restart; set API_SECRET_KEY in production.'
    )
    _SECRET = secrets.token_hex(32)
_ALGORITHM = 'HS256'
_ACCESS_EXPIRE = timedelta(hours=1)
_REFRESH_EXPIRE = timedelta(days=7)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/login')

# Web (PWA) refresh-token cookie: mirrors the JSON body's refresh_token into an
# httpOnly cookie so mobile/services/auth.ts never needs to persist it in
# localStorage on web (self-pentest finding: a stolen refresh token there gave
# durable, replayable access with no OS-level protection). Native clients
# ignore Set-Cookie entirely and keep using the JSON body via SecureStore --
# this is purely additive, not a breaking change to the existing API contract.
_REFRESH_COOKIE_NAME = 'refresh_token'
_REFRESH_COOKIE_PATH = '/auth'
# Local dev runs over plain http://localhost, where a Secure cookie would never
# be stored/sent by the browser at all. Render (and any real deployment) is
# always https, so default secure=True and only relax it when explicitly told.
_COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'true').strip().lower() not in ('0', 'false', 'no')


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=token,
        max_age=int(_REFRESH_EXPIRE.total_seconds()),
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite='strict',
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)


def _load_creds() -> dict | None:
    cfg = get_config()
    path = Path(cfg['db_path']).parent / '.credentials.json'
    try:
        creds = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    # Read on every request now (see _account), so a file that parses but isn't
    # the expected object must degrade to "no built-in admin", not a 500 for all.
    return creds if isinstance(creds, dict) else None


def _verify_password(password: str, hash_b64: str) -> bool:
    raw = base64.b64decode(hash_b64)
    salt, dk = raw[:32], raw[32:]
    candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return hmac.compare_digest(candidate, dk)


# Fixed dummy hash in the same salt[32]+dk[32] base64 format _verify_password expects,
# used to run the same PBKDF2 work on a missing-credentials-file/unknown-username path
# as on a wrong-password-for-a-real-username path — see authenticate() below.
_DUMMY_HASH = base64.b64encode(secrets.token_bytes(64)).decode('ascii')


# Least privilege: each role is a superset of the one before it.
#   viewer   -- read-only
#   operator -- viewer + submit gauge readings
#   admin    -- operator + bulk import, delete regulated records, manage users
ROLES = ('viewer', 'operator', 'admin')


def builtin_username() -> str | None:
    """Username of the original single login (data/.credentials.json), if any."""
    creds = _load_creds()
    return creds.get('username') if creds else None


def _live_user(username: str) -> dict | None:
    """The per-person account, or None if missing, disabled or holding a role
    this code doesn't know (e.g. a row edited by hand)."""
    user = users.get_user(username)
    if not user or user['disabled'] or user['role'] not in ROLES:
        return None
    return user


def _account(username: str) -> tuple[str, int] | None:
    """(role, token_version) of a usable account, else None.

    The original single login is a built-in admin, so turning RBAC on can never
    lock out the one account a lab already uses. Its token_version is always 0
    and per-person accounts start at 1, so a per-person token is never accepted
    as the built-in admin's even if the built-in is later renamed onto that
    person's username."""
    if username == builtin_username():
        return 'admin', 0
    user = _live_user(username)
    return (user['role'], user['token_version']) if user else None


def resolve_role(username: str) -> str | None:
    """The account's current role, or None if it is disabled or doesn't exist."""
    account = _account(username)
    return account[0] if account else None


def authenticate(username: str, password: str) -> bool:
    creds = _load_creds()
    if creds and creds.get('username') == username:
        return _verify_password(password, creds['hash'])
    user = _live_user(username)
    if user:
        return _verify_password(password, user['password_hash'])
    # Constant-time dummy verify (same 600k-iteration PBKDF2 cost as the real
    # path) to prevent username enumeration via response-time differences.
    _verify_password(password, _DUMMY_HASH)
    return False


def hash_password(password: str) -> str:
    """Same PBKDF2-SHA256 format _verify_password expects (salt[:32] + dk)."""
    salt = secrets.token_bytes(32)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600_000)
    return base64.b64encode(salt + dk).decode('ascii')


def ensure_bootstrap_credentials(db_path: str) -> None:
    """Create data/.credentials.json from BOOTSTRAP_USERNAME/BOOTSTRAP_PASSWORD
    env vars on first boot of a fresh deploy (e.g. a new Render disk), where
    setup_credentials.py's interactive prompt can't run. Never overwrites an
    existing credentials file, and does nothing if either env var is unset —
    a deploy without them configured just can't log in yet, it doesn't crash.

    Set BOOTSTRAP_FORCE_RESET=true to delete an existing credentials file and
    recreate it from the current BOOTSTRAP_USERNAME/BOOTSTRAP_PASSWORD — the
    only way to recover from a wrong/forgotten bootstrap password when there's
    no shell access to just delete the file directly (e.g. free-tier Render).
    Unset BOOTSTRAP_FORCE_RESET again after the next successful boot.
    """
    creds_path = Path(db_path).parent / '.credentials.json'
    force_reset = os.environ.get('BOOTSTRAP_FORCE_RESET', '').strip().lower() in ('1', 'true', 'yes')

    if creds_path.exists():
        if not force_reset:
            logging.getLogger('uvicorn.error').info(
                'Bootstrap credentials check: %s already exists — skipping (this is '
                'expected on every boot after the first). Set BOOTSTRAP_FORCE_RESET=true '
                'to replace it if the login is wrong/forgotten.', creds_path,
            )
            return
        logging.getLogger('uvicorn.error').warning(
            'BOOTSTRAP_FORCE_RESET is set — deleting %s and recreating it from '
            'BOOTSTRAP_USERNAME/BOOTSTRAP_PASSWORD. Unset BOOTSTRAP_FORCE_RESET '
            'after this boot succeeds, or every future restart will do this again.',
            creds_path,
        )
        creds_path.unlink()
    username = os.environ.get('BOOTSTRAP_USERNAME', '').strip()
    password = os.environ.get('BOOTSTRAP_PASSWORD', '')
    if not username or not password:
        logging.getLogger('uvicorn.error').warning(
            'No login exists yet (%s not found) and BOOTSTRAP_USERNAME/'
            'BOOTSTRAP_PASSWORD are not both set — nobody can log in until '
            'both are configured and the service restarts.', creds_path,
        )
        return
    if len(password) < 12:
        logging.getLogger('uvicorn.error').warning(
            'BOOTSTRAP_PASSWORD is set but shorter than 12 characters — refusing '
            'to create credentials. Set a longer BOOTSTRAP_PASSWORD and restart.'
        )
        return
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(
        json.dumps({'username': username, 'hash': hash_password(password)}),
        encoding='utf-8',
    )
    logging.getLogger('uvicorn.error').info(
        'Bootstrapped credentials for user %r at %s', username, creds_path,
    )


def create_tokens(username: str, lab_id: str) -> dict:
    now = datetime.now(timezone.utc)
    # 'ver' pins the token to the account's current token_version -- see
    # _live_role(). (An unknown username gets 0; such a token is refused anyway.)
    account = _account(username)
    ver = account[1] if account else 0
    access = jwt.encode(
        {'sub': username, 'lab_id': lab_id, 'exp': now + _ACCESS_EXPIRE, 'type': 'access',
         'jti': uuid.uuid4().hex, 'ver': ver},
        _SECRET, algorithm=_ALGORITHM,
    )
    refresh = jwt.encode(
        {'sub': username, 'lab_id': lab_id, 'exp': now + _REFRESH_EXPIRE, 'type': 'refresh',
         'jti': uuid.uuid4().hex, 'ver': ver},
        _SECRET, algorithm=_ALGORITHM,
    )
    return {'access_token': access, 'refresh_token': refresh, 'token_type': 'bearer'}


def _is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    cfg = get_config()
    conn = sqlite3.connect(cfg['db_path'], timeout=30)
    try:
        row = conn.execute('SELECT 1 FROM revoked_tokens WHERE jti = ?', [jti]).fetchone()
        return row is not None
    finally:
        conn.close()


def revoke_token(payload: dict) -> None:
    """Add a decoded token's jti to the revocation list so get_current_user()/
    get_refresh_payload() reject it immediately, instead of trusting it until
    its natural expiry (up to 7 days for a refresh token)."""
    jti = payload.get('jti')
    if not jti:
        return
    cfg = get_config()
    now = datetime.now(timezone.utc)
    exp_claim = payload.get('exp')
    expires_at = (
        datetime.fromtimestamp(exp_claim, tz=timezone.utc) if exp_claim else now
    )
    conn = sqlite3.connect(cfg['db_path'], timeout=30)
    try:
        conn.execute(
            'INSERT OR REPLACE INTO revoked_tokens (jti, expires_at, revoked_at) VALUES (?,?,?)',
            [jti, expires_at.isoformat(), now.isoformat()],
        )
        # Opportunistic cleanup so the table doesn't grow forever — a token past
        # its own expiry is already rejected by jwt.decode(), so its revocation
        # entry is no longer doing any work.
        conn.execute('DELETE FROM revoked_tokens WHERE expires_at < ?', [now.isoformat()])
        conn.commit()
    finally:
        conn.close()


def _decode(token: str) -> dict:
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Token expired')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='Invalid token')
    if _is_revoked(payload.get('jti')):
        raise HTTPException(status_code=401, detail='Token has been revoked')
    return payload


def _live_role(payload: dict) -> str:
    """The role of the account a decoded token belongs to, or 401.

    Looked up on every request rather than trusted from the token, so demoting
    or disabling an account takes effect immediately instead of when its token
    happens to expire. The token's 'ver' must also match the account's current
    token_version: a password reset or an enable/disable bumps it, so tokens
    issued before that are dead for good (tokens minted before this claim
    existed have none and count as 0, which only the built-in admin matches)."""
    account = _account(payload.get('sub', ''))
    if account is None or account[1] != payload.get('ver', 0):
        raise HTTPException(status_code=401, detail='Account is disabled, changed or no longer exists')
    return account[0]


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    payload = _decode(token)
    if payload.get('type') != 'access':
        raise HTTPException(status_code=401, detail='Access token required')
    return {**payload, 'username': payload['sub'], 'role': _live_role(payload)}


def require_role(minimum: str):
    """Dependency: the caller must hold `minimum` or a higher role (see ROLES)."""
    needed = ROLES.index(minimum)

    def _require(user: dict = Depends(get_current_user)) -> dict:
        role = user.get('role')
        if role not in ROLES or ROLES.index(role) < needed:
            raise HTTPException(status_code=403, detail=f'This action requires the {minimum} role.')
        return user

    return _require


def _extract_refresh_token(
    authorization: str | None = Header(None),
    refresh_token_cookie: str | None = Cookie(None, alias=_REFRESH_COOKIE_NAME),
) -> str:
    """Native clients send the refresh token as `Authorization: Bearer ...`
    (unchanged). Web clients send no such header and instead rely on the
    httpOnly cookie set_refresh_cookie() wrote on login/refresh -- the browser
    attaches it automatically to a same-origin request made with
    `credentials: 'include'`."""
    if authorization and authorization.lower().startswith('bearer '):
        return authorization[len('bearer '):]
    if refresh_token_cookie:
        return refresh_token_cookie
    raise HTTPException(status_code=401, detail='Not authenticated')


def get_refresh_payload(token: str = Depends(_extract_refresh_token)) -> dict:
    payload = _decode(token)
    if payload.get('type') != 'refresh':
        raise HTTPException(status_code=401, detail='Refresh token required')
    # A disabled/reset account must not keep minting access tokens from a
    # refresh token issued while it was still good (valid for 7 days).
    _live_role(payload)
    return payload
