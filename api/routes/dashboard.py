import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()
_log = logging.getLogger('cyclotron.dashboard')


def _beam_trend(db_path: str) -> list:
    """Last 14 days of beam_daily rows (recent-first). Empty (not erroring)
    if the table has no rows yet, or doesn't exist yet (fresh cloud DB with
    no ingestion run)."""
    conn = get_conn(db_path)
    try:
        latest = conn.execute("SELECT MAX(date) FROM beam_daily").fetchone()
        if not latest or not latest[0]:
            return []
        rows = conn.execute(
            "SELECT date, param, mean, min, max FROM beam_daily "
            "WHERE date >= date(?, '-13 days') "
            "ORDER BY date DESC, param ASC LIMIT 500",
            [latest[0]],
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _gauge_history(db_path: str, lab_id: str) -> list:
    """Most recent 20 gauge readings for this lab. Empty (not erroring) if
    there are none yet."""
    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT gauge_name, timestamp, value, unit, is_alert, photo_path "
            "FROM gauge_readings WHERE lab_id=? ORDER BY timestamp DESC LIMIT 20",
            [lab_id],
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


@router.get('/dashboard')
def get_dashboard(user: dict = Depends(get_current_user)):
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    db_path = cfg.get('db_path')

    payload = None

    # Primary: synced dashboard written by the on-prem data bridge
    if db_path:
        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT payload FROM synced_dashboard WHERE lab_id=?", [lab_id]
            ).fetchone()
            if row:
                payload = json.loads(row['payload'])
        finally:
            conn.close()

    # Fallback: local dashboard.json (works when API runs on-prem alongside the watcher)
    if payload is None:
        local_path = cfg.get('dashboard_path')
        if local_path:
            p = Path(local_path)
            if p.exists():
                try:
                    payload = json.loads(p.read_text(encoding='utf-8'))
                except (json.JSONDecodeError, OSError):
                    _log.warning('Dashboard read failed', exc_info=True)
                    raise HTTPException(500, detail='Dashboard data temporarily unavailable')

    if payload is None:
        # No on-prem sync has ever run (monitor/cloud_sync.py -> POST
        # /sync/dashboard) — component/alert data genuinely isn't available.
        # Degrade gracefully rather than 503: beam_trend/gauge_history below
        # are independently queryable and may have real data (e.g. pushed
        # directly via /api/admin/import/*) even when this never happened.
        payload = {'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'components': []}

    payload['beam_trend'] = _beam_trend(db_path) if db_path else []
    payload['gauge_history'] = _gauge_history(db_path, lab_id) if db_path else []
    return payload
