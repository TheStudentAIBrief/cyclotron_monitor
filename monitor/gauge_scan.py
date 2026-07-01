"""
Shared gauge-lookup + QR-payload logic for both the /scan web endpoints
(api/routes/scan.py) and the printable label generator
(scripts/generate_gauge_qr_labels.py) -- same pattern as
monitor/eur_form_parser.py being shared between api/routes/gauges.py and
scripts/import_eur_forms.py.

Read-only (SELECT only). Never imports from api/ so scripts can use it
standalone without pulling in the FastAPI app.
"""
import sqlite3

import qrcode


def fetch_gauges(db_path):
    """Return one dict per gauge: the latest reading, excluding rows with an
    empty gauge_name or empty location."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT gauge_name, location, unit, value, alert_lo, alert_hi,
                   action_lo, action_hi, confidence, timestamp
            FROM gauge_readings
            WHERE gauge_name != '' AND location != ''
            ORDER BY timestamp ASC
            """
        ).fetchall()
    finally:
        conn.close()

    latest = {}
    for row in rows:
        latest[row["gauge_name"]] = dict(row)
    return list(latest.values())


def gauge_scan_url(base_url, gauge_name):
    return f"{base_url.rstrip('/')}/scan/{gauge_name}"


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
