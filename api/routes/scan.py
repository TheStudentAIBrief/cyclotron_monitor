"""
QR-code scan landing page — GET /scan/{gauge_name}, called by whatever scans the
printed gauge label (phone camera, or the co-founder's separate GxP eQMS system).
Deliberately unauthenticated (same idiom as api/routes/sync.py): no JWT, since the
scanner has no way to log in first. Read-only.

GET /scan (the index below) is different: it is not a single-gauge QR landing
page, it enumerates the entire facility's gauge inventory/locations/thresholds
in one response, so it requires the same JWT auth as the rest of /api/* --
unlike an individual gauge scan, there is no "scanner with no way to log in
first" justification for the full inventory listing.

Because the single-gauge routes need no login and gauge names are short and
predictable, they are rate limited per caller (see _throttle): a few lookups of
names that don't exist and the caller is refused for the rest of the minute.
"""
import html
import os
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn
from api.ratelimit import PerMinuteCounter, client_key
from monitor.gauge_scan import build_qr, fetch_gauges, gauge_scan_url

router = APIRouter()

_QR_BG = '#1a1a2e'

# Per caller, per minute. A technician scanning labels makes a handful of requests
# and never asks for a gauge that doesn't exist; someone walking through names
# mostly misses. Both are counted in-process (single server instance).
MAX_UNKNOWN_PER_MINUTE = int(os.environ.get('SCAN_MAX_UNKNOWN_PER_MIN', '10'))
MAX_PER_MINUTE = int(os.environ.get('SCAN_MAX_PER_MIN', '120'))
_requests = PerMinuteCounter()
_unknown = PerMinuteCounter()


def reset_rate_limits() -> None:
    _requests.reset()
    _unknown.reset()


def _refuse() -> None:
    raise HTTPException(status_code=429, detail='Too many requests. Try again in a minute.',
                        headers={'Retry-After': '60'})


def _throttle_guessing(request: Request) -> None:
    """Refuse a caller who has been guessing names.

    Once the guessing allowance is spent, EVERY single-gauge request from that
    caller is refused -- real gauges included -- so the reply can't be used to
    tell which names exist."""
    if _unknown.count(client_key(request)) >= MAX_UNKNOWN_PER_MINUTE:
        _refuse()


def _throttle(request: Request) -> None:
    """As _throttle_guessing, plus a cap on bulk reading of gauge pages.

    The QR image route only uses _throttle_guessing: the logged-in index page
    loads one image per gauge, and an image of a real gauge gives away nothing
    but the address it encodes."""
    _throttle_guessing(request)
    if _requests.add(client_key(request)) > MAX_PER_MINUTE:
        _refuse()


def _unknown_gauge(request: Request, gauge_name: str) -> JSONResponse:
    # Counted here, and refused here if that takes the caller over the allowance:
    # a burst of requests can all pass _throttle_guessing before any miss is counted.
    if _unknown.add(client_key(request)) > MAX_UNKNOWN_PER_MINUTE:
        _refuse()
    return JSONResponse(status_code=404, content={'error': 'unknown gauge', 'gauge_name': gauge_name})


def _find_gauge(gauge_name: str):
    """Latest reading for a gauge in this server's lab, or None. Both single-gauge
    routes use this, so they always agree on which gauges exist (and neither
    reads the whole table per request)."""
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        return conn.execute(
            "SELECT location, value, unit, timestamp, confidence, "
            "alert_lo, alert_hi, action_lo, action_hi "
            "FROM gauge_readings "
            "WHERE lab_id=? AND gauge_name=? AND location IS NOT NULL AND location != '' "
            "ORDER BY timestamp DESC LIMIT 1",
            [cfg.get('lab_id', 'default'), gauge_name],
        ).fetchone()
    finally:
        conn.close()


@router.get('/scan', dependencies=[Depends(get_current_user)])
def scan_index(request: Request):
    """Website listing every logged gauge, grouped by location, each with its
    QR code attached inline. Requires a valid access token (see module
    docstring) -- unlike /scan/{gauge_name}, this lists the whole facility."""
    cfg = get_config()
    base_url = str(request.base_url).rstrip('/')
    # This lab's gauges only -- the same set the single-gauge routes will answer for,
    # so every QR image the page embeds exists.
    gauges = sorted(fetch_gauges(cfg['db_path'], lab_id=cfg.get('lab_id', 'default')),
                    key=lambda g: (g['location'], g['gauge_name']))

    sections = []
    current_location = None
    for g in gauges:
        if g['location'] != current_location:
            current_location = g['location']
            sections.append(f'<h2>{html.escape(str(current_location))}</h2>')
        safe_name = quote(g['gauge_name'], safe='')
        e_name = html.escape(str(g['gauge_name']))
        sections.append(f"""
<div class="gauge">
  <img src="/scan/{safe_name}/qr.png" width="140" height="140" alt="QR for {e_name}">
  <div>
    <a href="/scan/{safe_name}"><strong>{e_name}</strong></a><br>
    {html.escape(str(g['value']))} {html.escape(str(g['unit']))} &middot; {html.escape(str(g['timestamp']))}<br>
    alert {html.escape(str(g['alert_lo']))}&ndash;{html.escape(str(g['alert_hi']))} &middot; action {html.escape(str(g['action_lo']))}&ndash;{html.escape(str(g['action_hi']))}
  </div>
</div>""")

    page = f"""<!DOCTYPE html>
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
    return HTMLResponse(content=page)


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


@router.get('/scan/{gauge_name}/qr.png', dependencies=[Depends(_throttle_guessing)])
def scan_qr_png(gauge_name: str, request: Request):
    """The QR image itself -- what /scan (the index) embeds inline, and what
    a printed label's QR encodes when scanned."""
    base_url = str(request.base_url).rstrip('/')
    if _find_gauge(gauge_name) is None:
        return _unknown_gauge(request, gauge_name)
    _throttle_guessing(request)   # the allowance may have run out while this request was in flight
    url = gauge_scan_url(base_url, gauge_name)
    qr_image = build_qr(url).make_image(fill_color='white', back_color=_QR_BG).convert('RGB')
    buf = BytesIO()
    qr_image.save(buf, format='PNG')
    return Response(content=buf.getvalue(), media_type='image/png')


@router.get('/scan/{gauge_name}', dependencies=[Depends(_throttle)])
def scan_gauge(gauge_name: str, request: Request, format: str = Query(None)):
    row = _find_gauge(gauge_name)
    if row is None:
        return _unknown_gauge(request, gauge_name)
    _throttle_guessing(request)   # the allowance may have run out while this request was in flight

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

    e_name = html.escape(str(gauge_name))
    page = f"""<!DOCTYPE html>
<html>
<head>
<title>{e_name} — Gauge Scan</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
<h1>{e_name}</h1>
<p>Location: {html.escape(str(row['location']))}</p>
<p>Reading: {html.escape(str(row['value']))} {html.escape(str(row['unit']))}</p>
<p>Timestamp: {html.escape(str(row['timestamp']))}</p>
<p>Confidence: {html.escape(str(row['confidence']))}</p>
<table>
<tr><td>Alert Lo</td><td>{html.escape(str(row['alert_lo']))}</td></tr>
<tr><td>Alert Hi</td><td>{html.escape(str(row['alert_hi']))}</td></tr>
<tr><td>Action Lo</td><td>{html.escape(str(row['action_lo']))}</td></tr>
<tr><td>Action Hi</td><td>{html.escape(str(row['action_hi']))}</td></tr>
</table>
</body>
</html>"""
    return HTMLResponse(content=page)
