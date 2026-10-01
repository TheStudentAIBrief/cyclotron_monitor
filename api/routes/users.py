"""Admin-only user management: create per-person accounts and set their role.

Every change is written to audit_log by api/users.py (who granted what to whom,
never the password). The built-in admin (data/.credentials.json) is not managed
here; it is changed only via BOOTSTRAP_* env vars, see api/auth.py.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from api import users
from api.auth import ROLES, builtin_username, hash_password, require_role

router = APIRouter()
_admin = require_role('admin')

# Lowercase only: look-alike names (Root-Admin vs root-admin) would make the
# audit log's actor column ambiguous.
_USERNAME_RE = re.compile(r'^[a-z0-9._-]{1,64}$')
_MIN_PASSWORD_LEN = 12   # same floor as BOOTSTRAP_PASSWORD / setup_credentials.py


class UserCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str
    password: str
    role: str


class UserUpdate(BaseModel):
    # extra='forbid': a mistyped field (e.g. "disable") must be an error, not a
    # 200 that leaves the admin believing the account was disabled.
    model_config = ConfigDict(extra='forbid')
    role: str | None = None
    disabled: bool | None = None
    password: str | None = None


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(ROLES)}.")


def _check_password(password: str) -> None:
    if len(password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400, detail=f'Password must be at least {_MIN_PASSWORD_LEN} characters.')


@router.get('/admin/users')
def list_users(admin: dict = Depends(_admin)):
    return users.list_users()


@router.post('/admin/users', status_code=201)
def create_user(req: UserCreate, admin: dict = Depends(_admin)):
    if not _USERNAME_RE.fullmatch(req.username):
        raise HTTPException(
            status_code=400,
            detail='Username must be 1-64 characters: lowercase letters, digits, dot, dash, underscore.')
    _check_role(req.role)
    _check_password(req.password)
    # The built-in admin isn't in the users table, so create_user() alone wouldn't
    # stop a new account taking (a case-variant of) its name.
    shadows_builtin = req.username == (builtin_username() or '').lower()
    if shadows_builtin or not users.create_user(
            req.username, hash_password(req.password), req.role, admin['username']):
        raise HTTPException(status_code=409, detail='That username is already taken.')
    return {'username': req.username, 'role': req.role}


@router.post('/admin/users/{username}')
def update_user(username: str, req: UserUpdate, admin: dict = Depends(_admin)):
    if users.get_user(username) is None:
        raise HTTPException(status_code=404, detail='User not found')
    if req.role is None and req.disabled is None and req.password is None:
        raise HTTPException(status_code=400, detail='Nothing to change: send role, disabled or password.')
    if req.role is not None:
        _check_role(req.role)
    if req.password is not None:
        _check_password(req.password)
    return users.update_user(
        username, admin['username'], role=req.role, disabled=req.disabled,
        password_hash=hash_password(req.password) if req.password is not None else None,
    )
