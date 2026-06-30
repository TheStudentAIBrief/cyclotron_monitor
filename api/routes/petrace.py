import time
from fastapi import APIRouter, Depends, Query
from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn
from monitor.petrace_dashboard import compute_petrace_dashboard

router = APIRouter()

# In-memory cache for the PETrace dashboard (recomputed at most once per 5 minutes)
_dashboard_cache: dict = {'data': None, 'ts': 0.0}
_DASHBOARD_TTL = 300  # seconds


@router.get('/petrace/summary')
def petrace_summary(user: dict = Depends(get_current_user)):
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        agg = conn.execute("""
            SELECT
                COUNT(*)          AS batch_count,
                MAX(batch_no)     AS last_batch_no,
                MIN(batch_date)   AS first_date,
                MAX(batch_date)   AS last_date,
                SUM(total_muAh)   AS total_muAh,
                (SELECT foil_no FROM petrace_batches
                 ORDER BY batch_no DESC LIMIT 1) AS current_foil
            FROM petrace_batches
        """).fetchone()

        recent = conn.execute("""
            SELECT batch_no, batch_date, tracer_name, peak_target_uA,
                   avg_target_uA, total_muAh, foil_no, duration_s, row_count
            FROM petrace_batches
            ORDER BY batch_no DESC
            LIMIT 20
        """).fetchall()

        foil_changes = conn.execute("""
            SELECT batch_no, batch_date, foil_no
            FROM petrace_batches
            WHERE foil_no IS NOT NULL
            ORDER BY batch_no
        """).fetchall()

        # Detect foil change events (when foil_no increases)
        changes = []
        prev_foil = None
        for row in foil_changes:
            fn = row['foil_no']
            if prev_foil is not None and fn != prev_foil:
                changes.append({'batch_no': row['batch_no'], 'batch_date': row['batch_date'],
                                 'old_foil': prev_foil, 'new_foil': fn})
            prev_foil = fn

        return {
            'batch_count': agg['batch_count'] or 0,
            'last_batch_no': agg['last_batch_no'],
            'first_date': agg['first_date'],
            'last_date': agg['last_date'],
            'total_muAh': round(agg['total_muAh'] or 0, 2),
            'current_foil': agg['current_foil'],
            'recent_batches': [dict(r) for r in recent],
            'foil_changes': changes[-10:],
        }
    finally:
        conn.close()


@router.get('/petrace/dashboard')
def petrace_dashboard(user: dict = Depends(get_current_user)):
    now = time.monotonic()
    if _dashboard_cache['data'] is not None and now - _dashboard_cache['ts'] < _DASHBOARD_TTL:
        return _dashboard_cache['data']

    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        rows = conn.execute("""
            SELECT batch_no, batch_date, foil_no, peak_target_uA, avg_target_uA,
                   total_muAh, rf_efficiency, peak_vacuum_P, avg_vacuum_P,
                   avg_arc_I, row_count, tracer_num, tracer_name, duration_s
            FROM petrace_batches
            ORDER BY batch_no ASC
        """).fetchall()
        result = compute_petrace_dashboard([dict(r) for r in rows])
        _dashboard_cache['data'] = result
        _dashboard_cache['ts'] = now
        return result
    finally:
        conn.close()


@router.get('/petrace/batches')
def petrace_batches(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    cfg = get_config()
    offset = (page - 1) * per_page
    conn = get_conn(cfg['db_path'])
    try:
        rows = conn.execute("""
            SELECT * FROM petrace_batches
            ORDER BY batch_no DESC
            LIMIT ? OFFSET ?
        """, [per_page, offset]).fetchall()
        return {'page': page, 'per_page': per_page, 'items': [dict(r) for r in rows]}
    finally:
        conn.close()
