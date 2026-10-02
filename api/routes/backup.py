"""Admin-only: take an off-box copy of the cloud database, and check the audit log.

The cloud data is a single SQLite file on a single disk with no shell access, so
the API is the only way to get a copy off the box (scripts/backup_cloud_db.py
does it on a schedule). The copy includes every account's password hash, which
is why it is admin-only and why taking one is itself written to the audit log.
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from api import audit
from api.auth import require_role
from api.config import get_config
from api.db_cloud import get_conn
from api.ratelimit import client_key, forwarded_for

router = APIRouter()
_admin = require_role('admin')


@router.get('/admin/backup')
def download_backup(admin: dict = Depends(_admin)):
    cfg = get_config()
    fd, tmp_path = tempfile.mkstemp(prefix='petlab-backup-', suffix='.db')
    os.close(fd)
    try:
        conn = get_conn(cfg['db_path'])
        try:
            # Recorded first so the copy contains the record of its own making.
            audit.write(conn, 'db_backup', admin['username'], cfg.get('lab_id', 'default'), '{}')
            conn.commit()
            dest = sqlite3.connect(tmp_path)
            try:
                conn.backup(dest)   # consistent snapshot even while others are writing
            finally:
                dest.close()
        finally:
            conn.close()
    except Exception:
        os.unlink(tmp_path)
        raise
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return FileResponse(
        tmp_path, media_type='application/octet-stream', filename=f'petlab-backup-{stamp}.db',
        headers={'Cache-Control': 'no-store'},   # a file full of password hashes
        background=BackgroundTask(os.unlink, tmp_path),
    )


@router.get('/admin/audit/verify')
def verify_audit_log(admin: dict = Depends(_admin)):
    conn = get_conn(get_config()['db_path'])
    try:
        return audit.verify(conn)
    finally:
        conn.close()


@router.get('/admin/client-address')
def client_address(request: Request, admin: dict = Depends(_admin)):
    """How this request reached the server -- what to check before setting
    TRUST_FORWARDED_FOR=1 behind a proxy (see api/ratelimit.py). If the last
    address in X-Forwarded-For is your own public address, the setting is safe
    to turn on; if it is the proxy's, turning it on changes nothing."""
    return {
        'connecting_address': request.client.host if request.client else None,
        'x_forwarded_for': forwarded_for(request),
        'counted_as_now': client_key(request),
        'counted_as_if_trusted': client_key(request, trust_forwarded=True),
    }
