import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn
from monitor.dashboard_writer import AVG_CYCLES

router = APIRouter()
_log = logging.getLogger('cyclotron.dashboard')


def _components_from_predictions(db_path: str, lab_id: str) -> list:
    """Reconstruct component cards from the latest predictions run for this lab.

    Used only when no on-prem bridge has ever POSTed a synced_dashboard payload.
    A cloud deploy fed only by the manual push_data_to_cloud.py workflow uploads
    the `predictions` table (via /api/admin/import/predictions) but never a
    dashboard.json, so without this the component cards would stay empty even
    though real predictions exist. Mirrors monitor/dashboard_writer.write_dashboard's
    field shape; fields the predictions table doesn't carry (counter_days, model
    read, trained_at) are None."""
    conn = get_conn(db_path)
    try:
        latest = conn.execute(
            "SELECT MAX(run_at) FROM predictions WHERE lab_id=?", [lab_id]
        ).fetchone()
        if not latest or not latest[0]:
            return []
        rows = conn.execute(
            "SELECT component, risk_score, days_estimate, alert_level, "
            "primary_signal, top_features FROM predictions "
            "WHERE lab_id=? AND run_at=? ORDER BY component",
            [lab_id, latest[0]],
        ).fetchall()
        last_maint = {
            r['component_label']: r['ts']
            for r in conn.execute(
                "SELECT component_label, MAX(timestamp) AS ts FROM maintenance_events "
                "WHERE lab_id=? GROUP BY component_label",
                [lab_id],
            )
        }
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    components = []
    for r in rows:
        try:
            reasons = json.loads(r['top_features']) if r['top_features'] else []
        except (json.JSONDecodeError, TypeError):
            reasons = []
        days = r['days_estimate']
        avg = AVG_CYCLES.get(r['component'], 60)
        pct = 0 if days is None else min(100, max(0, int(100 * (avg - days) / avg)))
        components.append({
            'name': r['component'],
            'risk_score': r['risk_score'],
            'days_estimate': days,
            'alert_level': r['alert_level'],
            'pct_life_used': pct,
            'last_maintenance': last_maint.get(r['component']),
            'top_reasons': reasons if isinstance(reasons, list) else [],
            'counter_days': None,
            'primary_signal': r['primary_signal'],
            'warning': None,
            'trained_at': None,
            'model_age_days': None,
            'component_type': 'wear',
        })
    return components


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
        # /sync/dashboard). Fall back to the predictions table, which the manual
        # push_data_to_cloud.py workflow uploads via /api/admin/import/predictions
        # — otherwise those cards would never surface. Empty (not 503) when there
        # are no predictions either; beam_trend/gauge_history below are queried
        # independently and may still have real data.
        components = _components_from_predictions(db_path, lab_id) if db_path else []
        payload = {
            'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'components': components,
        }

    payload['beam_trend'] = _beam_trend(db_path) if db_path else []
    payload['gauge_history'] = _gauge_history(db_path, lab_id) if db_path else []
    return payload
