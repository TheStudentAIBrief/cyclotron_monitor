"""
QR-code scan landing page — GET /scan/{gauge_name}, called by whatever scans the
printed gauge label (phone camera, or the co-founder's separate GxP eQMS system).
Deliberately unauthenticated (same idiom as api/routes/sync.py): no JWT, since the
scanner has no way to log in first. Read-only.
"""
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()


@router.get('/scan/{gauge_name}')
def scan_gauge(gauge_name: str, request: Request, format: str = Query(None)):
    cfg = get_config()
    lab_id = cfg.get('lab_id', 'default')
    conn = get_conn(cfg['db_path'])
    try:
        row = conn.execute(
            "SELECT location, value, unit, timestamp, confidence, "
            "alert_lo, alert_hi, action_lo, action_hi "
            "FROM gauge_readings "
            "WHERE lab_id=? AND gauge_name=? AND location IS NOT NULL AND location != '' "
            "ORDER BY timestamp DESC LIMIT 1",
            [lab_id, gauge_name],
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return JSONResponse(
            status_code=404,
            content={'error': 'unknown gauge', 'gauge_name': gauge_name},
        )

    scan_url = f"{str(request.base_url).rstrip('/')}/scan/{quote(gauge_name, safe='')}"

    if format == 'json':
        return JSONResponse(content={
            'gauge_name': gauge_name,
            'location': row['location'],
            'latest_reading': {
                'value': row['value'],
                'unit': row['unit'],
                'timestamp': row['timestamp'],
                'confidence': row['confidence'],
            },
            'thresholds': {
                'alert_lo': row['alert_lo'],
                'alert_hi': row['alert_hi'],
                'action_lo': row['action_lo'],
                'action_hi': row['action_hi'],
            },
            'scan_url': scan_url,
        })

    html = f"""<!DOCTYPE html>
<html>
<head><title>{gauge_name} — Gauge Scan</title></head>
<body>
<h1>{gauge_name}</h1>
<p>Location: {row['location']}</p>
<p>Reading: {row['value']} {row['unit']}</p>
<p>Timestamp: {row['timestamp']}</p>
<p>Confidence: {row['confidence']}</p>
<table>
<tr><td>Alert Lo</td><td>{row['alert_lo']}</td></tr>
<tr><td>Alert Hi</td><td>{row['alert_hi']}</td></tr>
<tr><td>Action Lo</td><td>{row['action_lo']}</td></tr>
<tr><td>Action Hi</td><td>{row['action_hi']}</td></tr>
</table>
</body>
</html>"""
    return HTMLResponse(content=html)
