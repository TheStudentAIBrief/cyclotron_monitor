import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

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
_REFRESH_EXPIRE = timedelta(days=30)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/login')


def _load_creds() -> dict | None:
    cfg = get_config()
    path = Path(cfg['db_path']).parent / '.credentials.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _verify_password(password: str, hash_b64: str) -> bool:
    raw = base64.b64decode(hash_b64)
    salt, dk = raw[:32], raw[32:]
    candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return hmac.compare_digest(candidate, dk)


def authenticate(username: str, password: str) -> bool:
    creds = _load_creds()
    if not creds:
        return False
    if creds.get('username') != username:
        return False
    return _verify_password(password, creds['hash'])


def _hash_password(password: str) -> str:
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
    """
    creds_path = Path(db_path).parent / '.credentials.json'
    if creds_path.exists():
        return
    username = os.environ.get('BOOTSTRAP_USERNAME', '').strip()
    password = os.environ.get('BOOTSTRAP_PASSWORD', '')
    if not username or not password:
        return
    if len(password) < 12:
        logging.getLogger('uvicorn.error').warning(
            'BOOTSTRAP_PASSWORD is set but shorter than 12 characters — refusing '
            'to create credentials. Set a longer BOOTSTRAP_PASSWORD and restart.'
        )
        return
    creds_path.parent.mkdir(parents=True, exist_ok=True)
    creds_path.write_text(
        json.dumps({'username': username, 'hash': _hash_password(password)}),
        encoding='utf-8',
    )
    logging.getLogger('uvicorn.error').info(
        'Bootstrapped credentials for user %r at %s', username, creds_path,
    )


def create_tokens(username: str, lab_id: str) -> dict:
    now = datetime.now(timezone.utc)
    access = jwt.encode(
        {'sub': username, 'lab_id': lab_id, 'exp': now + _ACCESS_EXPIRE, 'type': 'access'},
        _SECRET, algorithm=_ALGORITHM,
    )
    refresh = jwt.encode(
        {'sub': username, 'lab_id': lab_id, 'exp': now + _REFRESH_EXPIRE, 'type': 'refresh'},
        _SECRET, algorithm=_ALGORITHM,
    )
    return {'access_token': access, 'refresh_token': refresh, 'token_type': 'bearer'}


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Token expired')
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail='Invalid token')


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    payload = _decode(token)
    if payload.get('type') != 'access':
        raise HTTPException(status_code=401, detail='Access token required')
    return payload


def get_refresh_payload(token: str = Depends(oauth2_scheme)) -> dict:
    payload = _decode(token)
    if payload.get('type') != 'refresh':
        raise HTTPException(status_code=401, detail='Refresh token required')
    return payload
