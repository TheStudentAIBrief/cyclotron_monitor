"""Storage for per-person accounts (the `users` table in the cloud DB).

Pure persistence -- no hashing, no HTTP. Password hashing and role checks live
in api/auth.py; request validation lives in api/routes/users.py.

Every change is written to audit_log (who granted what to whom, never the
password or its hash) in the same transaction as the change itself, so an
account can't be altered without leaving a trace.
"""
import json
import sqlite3
from datetime import datetime, timezone

from api import audit
from api.config import get_config
from api.db_cloud import get_conn

_PUBLIC_COLUMNS = 'username, role, disabled, created_at, created_by'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _audit(conn: sqlite3.Connection, action: str, actor: str, detail: dict) -> None:
    audit.write(conn, action, actor, get_config().get('lab_id', 'default'), json.dumps(detail))


def get_user(username: str) -> dict | None:
    conn = get_conn(get_config()['db_path'])
    try:
        row = conn.execute('SELECT * FROM users WHERE username = ?', [username]).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users() -> list[dict]:
    """Every account, without its password hash."""
    conn = get_conn(get_config()['db_path'])
    try:
        rows = conn.execute(f'SELECT {_PUBLIC_COLUMNS} FROM users ORDER BY username').fetchall()
        return [{**dict(r), 'disabled': bool(r['disabled'])} for r in rows]
    finally:
        conn.close()


def create_user(username: str, password_hash: str, role: str, actor: str) -> bool:
    """Insert a new account. Returns False (and changes nothing) if the username
    is already taken."""
    conn = get_conn(get_config()['db_path'])
    try:
        conn.execute(
            'INSERT INTO users (username, password_hash, role, created_at, created_by) '
            'VALUES (?,?,?,?,?)',
            [username, password_hash, role, _now(), actor],
        )
        _audit(conn, 'user_create', actor, {'username': username, 'role': role})
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_user(username: str, actor: str, *, role: str | None = None,
                disabled: bool | None = None, password_hash: str | None = None) -> dict:
    """Change only the fields that were given; returns what changed (for the caller
    to report), with the password shown only as `password_reset: True`.

    A password reset or any enable/disable also bumps token_version, which
    invalidates every token issued before it -- so a reset really does evict
    whoever holds the old login, and re-enabling an account doesn't bring its old
    sessions back to life. A role change alone doesn't (it takes effect on the
    next request anyway), so promoting someone doesn't log them out.
    """
    fields = {'role': role, 'password_hash': password_hash,
              'disabled': None if disabled is None else int(disabled)}
    changes = {k: v for k, v in fields.items() if v is not None}
    assignments = [f'{k} = ?' for k in changes]
    if password_hash is not None or disabled is not None:
        assignments.append('token_version = token_version + 1')
    detail = {'username': username}
    if role is not None:
        detail['role'] = role
    if disabled is not None:
        detail['disabled'] = disabled
    if password_hash is not None:
        detail['password_reset'] = True
    conn = get_conn(get_config()['db_path'])
    try:
        conn.execute(f"UPDATE users SET {', '.join(assignments)} WHERE username = ?",
                     [*changes.values(), username])
        _audit(conn, 'user_update', actor, detail)
        conn.commit()
    finally:
        conn.close()
    return detail
