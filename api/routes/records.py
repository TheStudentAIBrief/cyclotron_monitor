import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()

# SQLite's OFFSET scans and discards every preceding row, so an unbounded page
# number lets an authenticated client force an effectively full-table-scan DoS
# via a single high-page request. A hard cap bounds the worst case (keyset/cursor
# pagination would remove the cost entirely, but that's a bigger API-shape change
# than this hardening pass covers).
_MAX_PAGE = 100_000


@router.get('/records/maintenance')
def get_maintenance(
    page: int = Query(1, ge=1, le=_MAX_PAGE),
    per_page: int = Query(50, ge=1, le=200),
    component: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    conn = get_conn(cfg['db_path'])
    offset = (page - 1) * per_page
    try:
        if component:
            rows = conn.execute(
                "SELECT timestamp, component_key, component_label, source_file "
                "FROM maintenance_events WHERE lab_id=? AND component_label LIKE ? "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [lab_id, f'%{component}%', per_page, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT timestamp, component_key, component_label, source_file "
                "FROM maintenance_events WHERE lab_id=? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [lab_id, per_page, offset],
            ).fetchall()
        return {'page': page, 'per_page': per_page, 'items': [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get('/records/predictions')
def get_predictions(
    page: int = Query(1, ge=1, le=_MAX_PAGE),
    per_page: int = Query(50, ge=1, le=200),
    component: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    conn = get_conn(cfg['db_path'])
    offset = (page - 1) * per_page
    try:
        if component:
            rows = conn.execute(
                "SELECT run_at, component, risk_score, days_estimate, alert_level, "
                "primary_signal, top_features FROM predictions WHERE lab_id=? AND component LIKE ? "
                "ORDER BY run_at DESC LIMIT ? OFFSET ?",
                [lab_id, f'%{component}%', per_page, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT run_at, component, risk_score, days_estimate, alert_level, "
                "primary_signal, top_features FROM predictions WHERE lab_id=? "
                "ORDER BY run_at DESC LIMIT ? OFFSET ?",
                [lab_id, per_page, offset],
            ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            if d.get('top_features'):
                try:
                    d['top_features'] = json.loads(d['top_features'])
                except json.JSONDecodeError:
                    pass
            items.append(d)
        return {'page': page, 'per_page': per_page, 'items': items}
    finally:
        conn.close()


@router.get('/records/events')
def get_events(
    page: int = Query(1, ge=1, le=_MAX_PAGE),
    per_page: int = Query(50, ge=1, le=200),
    code: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    conn = get_conn(cfg['db_path'])
    offset = (page - 1) * per_page
    try:
        if code:
            rows = conn.execute(
                "SELECT timestamp, severity, code, function, message FROM events "
                "WHERE lab_id=? AND code=? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [lab_id, code, per_page, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT timestamp, severity, code, function, message FROM events "
                "WHERE lab_id=? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [lab_id, per_page, offset],
            ).fetchall()
        return {'page': page, 'per_page': per_page, 'items': [dict(r) for r in rows]}
    finally:
        conn.close()
