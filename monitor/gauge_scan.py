"""
Shared gauge-lookup + QR-payload logic for both the /scan web endpoints
(api/routes/scan.py) and the printable label generator
(scripts/generate_gauge_qr_labels.py) -- same pattern as
monitor/eur_form_parser.py being shared between api/routes/gauges.py and
scripts/import_eur_forms.py.

Read-only (SELECT only). Never imports from api/ so scripts can use it
standalone without pulling in the FastAPI app.
"""
import hashlib
import hmac
import sqlite3
from urllib.parse import quote

import qrcode


def fetch_gauges(db_path, lab_id=None):
    """Return one dict per gauge: the latest reading, excluding rows with an
    empty gauge_name or empty location. With lab_id, only that lab's gauges
    (the cloud database holds a lab_id per reading)."""
    lab_clause, params = ("AND lab_id = ?", [lab_id]) if lab_id is not None else ("", [])
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT gauge_name, location, unit, value, alert_lo, alert_hi,
                   action_lo, action_hi, confidence, timestamp
            FROM gauge_readings
            WHERE gauge_name != '' AND location != '' """ + lab_clause + """
            ORDER BY timestamp ASC
            """, params
        ).fetchall()
    finally:
        conn.close()

    latest = {}
    for row in rows:
        latest[row["gauge_name"]] = dict(row)
    return list(latest.values())


def scan_code(secret, gauge_name):
    """The code a QR link carries for this gauge: 80 bits of an HMAC of the gauge
    name under the deployment's QR_LINK_SECRET. It cannot be worked out from the
    gauge name, so with QR_REQUIRE_CODE=1 a gauge page only opens for someone
    holding the printed label (api/routes/scan.py)."""
    return hmac.new(secret.encode(), gauge_name.encode(), hashlib.sha256).hexdigest()[:20]


def gauge_scan_url(base_url, gauge_name, code=None):
    # Quoted: a name with a space, '#' or '?' would otherwise cut the link short
    # (and drop the code) when a phone camera reads it.
    url = f"{base_url.rstrip('/')}/scan/{quote(gauge_name, safe='')}"
    return f"{url}?c={code}" if code else url


def build_qr(url):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr
