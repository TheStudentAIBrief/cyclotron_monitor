"""Regression test for the stored-XSS fix in api/routes/scan.py (security hardening,
2026-07-02): gauge_name/location/value/etc. are rendered into unauthenticated HTML
pages with no way for the visitor to log in first, so any DB-derived value must be
HTML-escaped before interpolation.
"""
import pytest
from starlette.requests import Request

from api.routes import scan


@pytest.fixture
def cloud_db(tmp_path, monkeypatch):
    from api import config as _config
    from api.db_cloud import init_cloud_tables, get_conn

    db_path = str(tmp_path / 'petlab.db')
    init_cloud_tables(db_path)
    monkeypatch.setenv('DATABASE_PATH', db_path)
    monkeypatch.setenv('LAB_ID', 'test-lab')
    _config.get_config.cache_clear()
    try:
        yield db_path
    finally:
        _config.get_config.cache_clear()


def _insert_gauge(db_path, gauge_name, location):
    from api.db_cloud import get_conn

    conn = get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO gauge_readings "
            "(lab_id, gauge_name, timestamp, value, unit, location, "
            " alert_lo, alert_hi, action_lo, action_hi, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ('test-lab', gauge_name, '2026-07-02T00:00:00Z', 42.0, 'psi', location,
             0, 100, 0, 100, 'high'),
        )
        conn.commit()
    finally:
        conn.close()


def _make_request(path):
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "query_string": b"",
    }
    return Request(scope)


_XSS_PAYLOAD = "<script>alert('stolen-session')</script>"


def test_scan_gauge_escapes_malicious_gauge_name(cloud_db):
    _insert_gauge(cloud_db, _XSS_PAYLOAD, "Cyclotron Room")
    response = scan.scan_gauge(_XSS_PAYLOAD, _make_request(f"/scan/{_XSS_PAYLOAD}"))
    body = response.body.decode()
    assert "<script>alert" not in body, "raw <script> tag leaked into the response — XSS regression"
    assert "&lt;script&gt;" in body, "expected the escaped form of the payload"


def test_scan_gauge_escapes_malicious_location(cloud_db):
    _insert_gauge(cloud_db, "PressureGauge1", _XSS_PAYLOAD)
    response = scan.scan_gauge("PressureGauge1", _make_request("/scan/PressureGauge1"))
    body = response.body.decode()
    assert "<script>alert" not in body, "raw <script> tag leaked into the response via location — XSS regression"
    assert "&lt;script&gt;" in body


def test_scan_gauge_json_format_is_unaffected(cloud_db):
    """format=json returns structured data, not HTML -- escaping must not corrupt it."""
    _insert_gauge(cloud_db, "PressureGauge2", "Room B")
    response = scan.scan_gauge("PressureGauge2", _make_request("/scan/PressureGauge2"), format="json")
    import json
    data = json.loads(response.body.decode())
    assert data["gauge_name"] == "PressureGauge2"
    assert data["location"] == "Room B"


def test_scan_index_escapes_malicious_values(cloud_db):
    _insert_gauge(cloud_db, "Gauge<img src=x onerror=alert(1)>", "Loc<script>evil()</script>")
    response = scan.scan_index(_make_request("/scan"))
    body = response.body.decode()
    assert "<img src=x onerror=" not in body
    assert "<script>evil()</script>" not in body
    assert "&lt;img src=x onerror=" in body
    assert "&lt;script&gt;evil()&lt;/script&gt;" in body
