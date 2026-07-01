"""Tests for the unauthenticated, read-only GET /scan/{gauge_name} endpoint.

TDD: api/routes/scan.py does not exist yet (this is written *before* the
implementation, per the task spec). Until scan.py is created, the
`from api.routes import scan` import below fails with ModuleNotFoundError --
that is the expected/correct "failing for the right reason" for this phase.

Once implemented, scan.py's router follows the same deliberate-no-JWT pattern
as api/routes/sync.py (see api/main.py's `app.include_router(sync.router,
prefix='')`), so it is included here the same way rather than relying on
api/main.py having been edited yet.
"""
import os
import tempfile
import uuid
from urllib.parse import quote

# Point the cloud DB at a fresh temp file so the app's lifespan init never
# touches the real 8GB production DB (see CRITICAL SAFETY RULE). A unique
# filename per test run avoids stale rows leaking in from earlier runs.
_DB_PATH = os.path.join(tempfile.gettempdir(), f'petlab_scan_test_{uuid.uuid4().hex}.db')
os.environ.setdefault('DATABASE_PATH', _DB_PATH)
# Another test module collected earlier in the same pytest process may have already
# set DATABASE_PATH first (setdefault is then a no-op) -- read back whatever is
# actually active so seeding and the app under test always agree on the same file
# (mirrors tests/test_delete_audit.py's `db = os.environ['DATABASE_PATH']` pattern).
_DB_PATH = os.environ['DATABASE_PATH']

from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402
from api.routes import scan  # noqa: E402  -- doesn't exist yet; expected import failure (TDD)

# scan.router is deliberately unauthenticated (no get_current_user dependency),
# same idiom as sync.router -- included plain, with no auth dependency list.
main.app.include_router(scan.router, prefix='')

_LAB_ID = 'petlabs-pretoria'
_GAUGE = 'Beam on Post'
_LOCATION = 'Control Room'
_ALERT_LO, _ALERT_HI = 0.5, 4.5
_ACTION_LO, _ACTION_HI = 0.2, 5.5

# Second gauge, different location -- lets the index-page tests verify more
# than one entry / more than one location group actually shows up.
_GAUGE_2 = 'G-201'
_LOCATION_2 = 'HVAC Room - Cyclotron HEPA'


def _seed():
    init_cloud_tables(_DB_PATH)
    conn = get_conn(_DB_PATH)
    try:
        # Older reading for the gauge under test -- must NOT be the one returned.
        conn.execute(
            "INSERT INTO gauge_readings "
            "(lab_id, gauge_name, timestamp, value, unit, location, "
            "alert_lo, alert_hi, action_lo, action_hi, confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [_LAB_ID, _GAUGE, '2026-06-30T10:00:00Z', 1.2, 'bar', _LOCATION,
             _ALERT_LO, _ALERT_HI, _ACTION_LO, _ACTION_HI, 'high'],
        )
        # Most recent reading for the gauge under test -- this is the one the
        # endpoint must return (latest by timestamp desc).
        conn.execute(
            "INSERT INTO gauge_readings "
            "(lab_id, gauge_name, timestamp, value, unit, location, "
            "alert_lo, alert_hi, action_lo, action_hi, confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [_LAB_ID, _GAUGE, '2026-06-30T12:00:00Z', 1.4, 'bar', _LOCATION,
             _ALERT_LO, _ALERT_HI, _ACTION_LO, _ACTION_HI, 'high'],
        )
        # A second, different gauge/location -- for the index-page tests.
        conn.execute(
            "INSERT INTO gauge_readings "
            "(lab_id, gauge_name, timestamp, value, unit, location, "
            "alert_lo, alert_hi, action_lo, action_hi, confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [_LAB_ID, _GAUGE_2, '2026-06-30T09:00:00Z', 2.3, 'Pa', _LOCATION_2,
             0.0, 5.0, -1.0, 6.0, 'ok'],
        )
        # Junk-data case mirroring the real "MMG005" row: gauge_name is present
        # but location is empty -- must be treated as unknown/404.
        conn.execute(
            "INSERT INTO gauge_readings "
            "(lab_id, gauge_name, timestamp, value, unit, location) "
            "VALUES (?,?,?,?,?,?)",
            [_LAB_ID, 'MMG005', '2026-06-30T09:00:00Z', 3.0, 'bar', ''],
        )
        conn.commit()
    finally:
        conn.close()


_seed()


def test_scan_html_default_response():
    with TestClient(main.app) as client:
        r = client.get(f'/scan/{quote(_GAUGE)}')
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('text/html')
    body = r.text
    assert _GAUGE in body
    assert _LOCATION in body
    assert '1.4' in body          # latest value (not the older 1.2 reading)
    assert 'bar' in body
    assert '2026-06-30T12:00:00Z' in body
    for threshold in (_ALERT_LO, _ALERT_HI, _ACTION_LO, _ACTION_HI):
        assert str(threshold) in body


def test_scan_json_format_matches_contract():
    with TestClient(main.app) as client:
        r = client.get(f'/scan/{quote(_GAUGE)}', params={'format': 'json'})
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('application/json')
    data = r.json()
    scan_url = data.pop('scan_url', None)
    assert data == {
        'gauge_name': _GAUGE,
        'location': _LOCATION,
        'latest_reading': {
            'value': 1.4,
            'unit': 'bar',
            'timestamp': '2026-06-30T12:00:00Z',
            'confidence': 'high',
        },
        'thresholds': {
            'alert_lo': _ALERT_LO,
            'alert_hi': _ALERT_HI,
            'action_lo': _ACTION_LO,
            'action_hi': _ACTION_HI,
        },
    }
    assert scan_url is not None
    assert scan_url.endswith(f'/scan/{quote(_GAUGE)}') or scan_url.endswith(f'/scan/{_GAUGE}')
    assert 'format=json' not in scan_url


def test_scan_unknown_gauge_returns_404():
    with TestClient(main.app) as client:
        r = client.get('/scan/DoesNotExist')
    assert r.status_code == 404
    body = r.json()
    assert body['error'] == 'unknown gauge'
    assert body['gauge_name'] == 'DoesNotExist'


def test_scan_empty_location_treated_as_unknown_404():
    """Mirrors the real junk-data 'MMG005' row -- a gauge_name that only ever
    has an empty location must 404, exactly like a gauge that doesn't exist."""
    with TestClient(main.app) as client:
        r = client.get('/scan/MMG005')
    assert r.status_code == 404
    body = r.json()
    assert body['error'] == 'unknown gauge'
    assert body['gauge_name'] == 'MMG005'


def test_scan_requires_no_authorization_header():
    with TestClient(main.app) as client:
        r = client.get(f'/scan/{quote(_GAUGE)}')  # no Authorization header sent at all
    assert r.status_code == 200


def test_scan_post_is_not_allowed():
    with TestClient(main.app) as client:
        r = client.post(f'/scan/{quote(_GAUGE)}', json={'value': 999})
    assert r.status_code == 405


# ── GET /scan (index website: every gauge, grouped, with its QR attached) ──

def test_scan_index_lists_every_real_gauge():
    with TestClient(main.app) as client:
        r = client.get('/scan')
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('text/html')
    body = r.text
    # Both real gauges present, with their locations.
    assert _GAUGE in body
    assert _LOCATION in body
    assert _GAUGE_2 in body
    assert _LOCATION_2 in body
    # The junk row (empty location) must never appear on the index.
    assert 'MMG005' not in body


def test_scan_index_uses_latest_reading_not_older_one():
    with TestClient(main.app) as client:
        r = client.get('/scan')
    assert '1.4' in r.text   # latest Beam on Post value
    assert 'bar' in r.text


def test_scan_index_embeds_a_qr_image_per_gauge():
    with TestClient(main.app) as client:
        r = client.get('/scan')
    body = r.text
    assert f'/scan/{quote(_GAUGE)}/qr.png' in body or f'/scan/{_GAUGE}/qr.png' in body
    assert f'/scan/{_GAUGE_2}/qr.png' in body


def test_scan_index_requires_no_authorization_header():
    with TestClient(main.app) as client:
        r = client.get('/scan')
    assert r.status_code == 200


# ── GET /scan/{gauge_name}/qr.png (the QR image itself) ────────────────────

def test_scan_qr_png_returns_image():
    with TestClient(main.app) as client:
        r = client.get(f'/scan/{quote(_GAUGE_2)}/qr.png')
    assert r.status_code == 200
    assert r.headers['content-type'] == 'image/png'
    assert len(r.content) > 0
    assert r.content[:8] == b'\x89PNG\r\n\x1a\n'   # PNG magic bytes


def test_scan_qr_png_is_deterministic_for_the_same_gauge():
    """Two requests for the same gauge's QR must encode the same URL -> the
    same pixels -> byte-identical PNGs (no hidden randomness/timestamps)."""
    with TestClient(main.app) as client:
        r1 = client.get(f'/scan/{quote(_GAUGE_2)}/qr.png')
        r2 = client.get(f'/scan/{quote(_GAUGE_2)}/qr.png')
    assert r1.content == r2.content


def test_scan_qr_png_differs_between_gauges():
    with TestClient(main.app) as client:
        r1 = client.get(f'/scan/{quote(_GAUGE)}/qr.png')
        r2 = client.get(f'/scan/{quote(_GAUGE_2)}/qr.png')
    assert r1.content != r2.content


def test_scan_qr_png_unknown_gauge_returns_404():
    with TestClient(main.app) as client:
        r = client.get('/scan/DoesNotExist/qr.png')
    assert r.status_code == 404


# ── Mobile "Add to Home Screen" support ─────────────────────────────────────

def test_scan_index_has_mobile_viewport_meta():
    """Without this, iOS/Android render the page zoomed-out/tiny by default."""
    with TestClient(main.app) as client:
        r = client.get('/scan')
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in r.text


def test_scan_index_is_addable_as_a_home_screen_app():
    """iOS 'Add to Home Screen' opens full-screen (no Safari chrome) and uses
    a named, iconned entry only when these are present."""
    with TestClient(main.app) as client:
        r = client.get('/scan')
    body = r.text
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in body
    assert '<meta name="apple-mobile-web-app-title" content="Gauges">' in body
    assert '<link rel="apple-touch-icon" href="/scan/icon.png">' in body
    assert '<meta name="theme-color" content="#1a1a2e">' in body


def test_scan_gauge_page_has_mobile_viewport_meta():
    with TestClient(main.app) as client:
        r = client.get(f'/scan/{quote(_GAUGE)}')
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in r.text


def test_scan_icon_png_returns_image():
    with TestClient(main.app) as client:
        r = client.get('/scan/icon.png')
    assert r.status_code == 200
    assert r.headers['content-type'] == 'image/png'
    assert r.content[:8] == b'\x89PNG\r\n\x1a\n'
    assert len(r.content) > 0
