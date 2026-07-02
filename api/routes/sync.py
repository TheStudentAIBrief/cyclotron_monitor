"""
Server-to-server sync endpoint — called by monitor/cloud_sync.py on the on-prem machine.
Protected by X-Sync-Key header (not JWT); never expose this key in the mobile app.
"""
import hmac
import json as _json
from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()


def _require_sync_key(x_sync_key: Optional[str] = Header(None, alias='X-Sync-Key')):
    # 404 (not 403) for every failure mode — missing header, wrong key, or unconfigured
    # key — so this route is indistinguishable from a nonexistent one to an unauthenticated
    # caller, preventing route-existence enumeration via a 403/404/422 status differential.
    cfg = get_config()
    expected = cfg.get('cloud_sync_key', '')
    # Constant-time comparison -- a plain `!=` short-circuits on the first differing
    # byte, letting a network attacker recover cloud_sync_key one byte at a time via
    # request timing (same class of issue BOOTSTRAP_PASSWORD is protected against
    # in api/auth.py's _verify_password).
    if not expected or not x_sync_key or not hmac.compare_digest(x_sync_key, expected):
        raise HTTPException(status_code=404)


@router.post('/sync/dashboard', dependencies=[Depends(_require_sync_key)])
def sync_dashboard(payload: dict):
    """Accept a full dashboard JSON payload from the on-prem data bridge."""
    cfg = get_config()
    lab_id = cfg.get('lab_id', 'default')
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn = get_conn(cfg['db_path'])
    try:
        conn.execute(
            "INSERT OR REPLACE INTO synced_dashboard (lab_id, payload, synced_at) VALUES (?,?,?)",
            [lab_id, _json.dumps(payload), ts],
        )
        conn.commit()
        return {'status': 'ok', 'lab_id': lab_id, 'synced_at': ts}
    finally:
        conn.close()
