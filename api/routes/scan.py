"""
QR-code scan landing page — GET /scan/{gauge_name}, called by whatever scans the
printed gauge label (phone camera, or the co-founder's separate GxP eQMS system).
Deliberately unauthenticated (same idiom as api/routes/sync.py): no JWT, since the
scanner has no way to log in first. Read-only.
"""
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from api.config import get_config
from api.db_cloud import get_conn
from monitor.gauge_scan import build_qr, fetch_gauges, gauge_scan_url

router = APIRouter()

_QR_BG = '#1a1a2e'


def _gauges_by_name(db_path):
    return {g['gauge_name']: g for g in fetch_gauges(db_path)}


@router.get('/scan')
def scan_index(request: Request):
    """Website listing every logged gauge, grouped by location, each with its
    QR code attached inline."""
    cfg = get_config()
    base_url = str(request.base_url).rstrip('/')
    gauges = sorted(fetch_gauges(cfg['db_path']), key=lambda g: (g['location'], g['gauge_name']))

    sections = []
    current_location = None
    for g in gauges:
        if g['location'] != current_location:
            current_location = g['location']
            sections.append(f'<h2>{current_location}</h2>')
        safe_name = quote(g['gauge_name'], safe='')
        sections.append(f"""
<div class="gauge">
  <img src="/scan/{safe_name}/qr.png" width="140" height="140" alt="QR for {g['gauge_name']}">
  <div>
    <a href="/scan/{safe_name}"><strong>{g['gauge_name']}</strong></a><br>
    {g['value']} {g['unit']} &middot; {g['timestamp']}<br>
    alert {g['alert_lo']}&ndash;{g['alert_hi']} &middot; action {g['action_lo']}&ndash;{g['action_hi']}
  </div>
</div>""")

    html = f"""<!DOCTYPE html>
<html>
<head>
<title>Gauges</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Gauges">
<meta name="theme-color" content="{_QR_BG}">
<link rel="apple-touch-icon" href="/scan/icon.png">
</head>
<body>
<h1>All Gauges ({len(gauges)})</h1>
{''.join(sections)}
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get('/scan/icon.png')
def scan_icon_png():
    """Home-screen icon for the /scan index (iOS 'Add to Home Screen' /
    Android 'Add to Home screen' both look for apple-touch-icon). Without
    this, the OS falls back to an auto-cropped screenshot of the page."""
    from PIL import Image, ImageDraw

    size = 180
    img = Image.new('RGB', (size, size), _QR_BG)
    draw = ImageDraw.Draw(img)
    # Simple gauge-dial glyph: an arc + a needle, in white.
    margin = 24
    draw.arc((margin, margin, size - margin, size - margin), start=135, end=45, fill='white', width=10)
    draw.line((size // 2, size // 2, size // 2 + 40, size // 2 - 40), fill='white', width=8)
    draw.ellipse((size // 2 - 10, size // 2 - 10, size // 2 + 10, size // 2 + 10), fill='white')
    buf = BytesIO()
    img.save(buf, format='PNG')
    return Response(content=buf.getvalue(), media_type='image/png')


@router.get('/scan/{gauge_name}/qr.png')
def scan_qr_png(gauge_name: str, request: Request):
    """The QR image itself -- what /scan (the index) embeds inline, and what
    a printed label's QR encodes when scanned."""
    cfg = get_config()
    base_url = str(request.base_url).rstrip('/')
    gauges = _gauges_by_name(cfg['db_path'])
    if gauge_name not in gauges:
        return JSONResponse(
            status_code=404,
            content={'error': 'unknown gauge', 'gauge_name': gauge_name},
        )
    url = gauge_scan_url(base_url, gauge_name)
    qr_image = build_qr(url).make_image(fill_color='white', back_color=_QR_BG).convert('RGB')
    buf = BytesIO()
    qr_image.save(buf, format='PNG')
    return Response(content=buf.getvalue(), media_type='image/png')


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
<head>
<title>{gauge_name} — Gauge Scan</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
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
