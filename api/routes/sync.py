"""
Server-to-server sync endpoint — called by monitor/cloud_sync.py on the on-prem machine.
Protected by X-Sync-Key header (not JWT); never expose this key in the mobile app.
"""
import json as _json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()


def _require_sync_key(x_sync_key: str = Header(..., alias='X-Sync-Key')):
    cfg = get_config()
    expected = cfg.get('cloud_sync_key', '')
    if not expected or x_sync_key != expected:
        raise HTTPException(status_code=403, detail='Invalid sync key')


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
