"""Rate limit on the unauthenticated QR gauge pages.

GET /scan/{gauge} needs no login by design (a QR label is scanned by a phone
camera), and gauge names are short and predictable -- so without a limit anyone
can walk through the names and read every gauge's location, thresholds and
latest value. Guessing produces misses; a normal scan does not. After a handful
of misses the caller is refused entirely for the rest of the minute, hits
included, so the response can't be used to tell which names exist.
"""
import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import config as _config
from api import ratelimit
from api.db_cloud import get_conn, init_cloud_tables
from api.routes import scan

_LAB = 'petlabs-pretoria'


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / 'petlab.db')
    monkeypatch.setenv('DATABASE_PATH', path)
    monkeypatch.setenv('LAB_ID', _LAB)
    monkeypatch.delenv('TRUST_FORWARDED_FOR', raising=False)
    _config.get_config.cache_clear()
    init_cloud_tables(path)
    conn = get_conn(path)
    conn.execute(
        "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, unit, location) "
        "VALUES (?,?,?,?,?,?)", [_LAB, '0096', '2026-01-01T00:00:00Z', 12.3, 'Pa', 'Cleanroom'])
    conn.commit()
    conn.close()
    scan.reset_rate_limits()
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        scan.reset_rate_limits()
        _config.get_config.cache_clear()


def test_a_normal_scan_works(client):
    assert client.get('/scan/0096').status_code == 200


def test_guessing_gauge_names_gets_the_caller_refused(client):
    statuses = [client.get(f'/scan/guess-{i}').status_code for i in range(scan.MAX_UNKNOWN_PER_MINUTE + 3)]
    assert statuses[:scan.MAX_UNKNOWN_PER_MINUTE] == [404] * scan.MAX_UNKNOWN_PER_MINUTE
    assert set(statuses[scan.MAX_UNKNOWN_PER_MINUTE:]) == {429}


def test_once_refused_a_real_gauge_is_refused_too_so_responses_reveal_nothing(client):
    for i in range(scan.MAX_UNKNOWN_PER_MINUTE):
        client.get(f'/scan/guess-{i}')
    assert client.get('/scan/0096').status_code == 429
    assert client.get('/scan/0096?format=json').status_code == 429


def test_the_qr_image_route_shares_the_same_budget(client):
    """/scan/{name}/qr.png answers 404 for an unknown gauge too, so it is the same oracle."""
    for i in range(scan.MAX_UNKNOWN_PER_MINUTE):
        assert client.get(f'/scan/guess-{i}/qr.png').status_code == 404
    assert client.get('/scan/0096/qr.png').status_code == 429
    assert client.get('/scan/0096').status_code == 429


def test_repeated_scans_of_real_gauges_are_not_treated_as_guessing(client):
    statuses = {client.get('/scan/0096').status_code for _ in range(scan.MAX_UNKNOWN_PER_MINUTE * 3)}
    assert statuses == {200}


def test_bulk_reading_is_capped_even_when_every_name_is_right(client, monkeypatch):
    monkeypatch.setattr(scan, 'MAX_PER_MINUTE', 5)
    scan.reset_rate_limits()
    statuses = [client.get('/scan/0096').status_code for _ in range(8)]
    assert statuses == [200] * 5 + [429] * 3


def test_the_index_page_can_load_a_qr_image_for_every_gauge(client, monkeypatch):
    """The logged-in /scan index shows one QR image per gauge, each fetched by the
    browser as its own request. Those must not use up the bulk-reading allowance,
    or a lab with many gauges would see broken images."""
    monkeypatch.setattr(scan, 'MAX_PER_MINUTE', 5)
    scan.reset_rate_limits()
    statuses = {client.get('/scan/0096/qr.png').status_code for _ in range(40)}
    assert statuses == {200}
    assert client.get('/scan/0096').status_code == 200      # and they didn't spend the page allowance


def test_the_refusal_lifts_after_a_minute(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, 'monotonic', lambda: now[0])
    scan.reset_rate_limits()
    for i in range(scan.MAX_UNKNOWN_PER_MINUTE):
        client.get(f'/scan/guess-{i}')
    assert client.get('/scan/0096').status_code == 429
    now[0] += 61
    assert client.get('/scan/0096').status_code == 200


def test_a_made_up_forwarded_header_does_not_dodge_the_limit(client):
    """By default the address the connection came from is used; a caller can't
    get a fresh allowance per request by inventing X-Forwarded-For values."""
    for i in range(scan.MAX_UNKNOWN_PER_MINUTE):
        client.get(f'/scan/guess-{i}', headers={'X-Forwarded-For': f'10.0.0.{i}'})
    assert client.get('/scan/0096', headers={'X-Forwarded-For': '10.9.9.9'}).status_code == 429


def test_behind_a_trusted_proxy_each_real_visitor_gets_their_own_allowance(client, monkeypatch):
    """With TRUST_FORWARDED_FOR=1 the LAST address in X-Forwarded-For is used --
    the one the proxy itself added -- so one visitor guessing doesn't lock out
    another, and values a caller puts in front of it are ignored."""
    monkeypatch.setenv('TRUST_FORWARDED_FOR', '1')
    for i in range(scan.MAX_UNKNOWN_PER_MINUTE):
        client.get(f'/scan/guess-{i}', headers={'X-Forwarded-For': f'1.1.1.{i}, 203.0.113.7'})
    assert client.get('/scan/0096', headers={'X-Forwarded-For': '203.0.113.7'}).status_code == 429
    assert client.get('/scan/0096', headers={'X-Forwarded-For': '9.9.9.9, 198.51.100.4'}).status_code == 200


def test_the_logged_in_index_is_not_subject_to_the_guessing_limit(client):
    from api.auth import get_current_user
    prev = main.app.dependency_overrides.get(get_current_user)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': _LAB, 'role': 'viewer'}
    try:
        for i in range(scan.MAX_UNKNOWN_PER_MINUTE):
            client.get(f'/scan/guess-{i}')
        assert client.get('/scan').status_code == 200
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev is not None:
            main.app.dependency_overrides[get_current_user] = prev


# ── gaps found by independent review ──────────────────────────────────────────

def test_a_burst_of_guesses_cannot_slip_past_the_allowance(client):
    """Requests that all pass the up-front check at the same moment must still be
    held to the allowance when their misses are counted."""
    main.app.dependency_overrides[scan._throttle] = lambda: None            # every request "got in"
    main.app.dependency_overrides[scan._throttle_guessing] = lambda: None
    try:
        pages = [client.get(f'/scan/guess-{i}').status_code for i in range(scan.MAX_UNKNOWN_PER_MINUTE + 4)]
    finally:
        main.app.dependency_overrides.pop(scan._throttle, None)
        main.app.dependency_overrides.pop(scan._throttle_guessing, None)
    assert pages.count(404) == scan.MAX_UNKNOWN_PER_MINUTE
    assert pages.count(429) == 4


def test_a_real_gauge_is_not_served_if_the_allowance_ran_out_while_the_request_was_in_flight(client, monkeypatch):
    real = scan._find_gauge

    def _slow_lookup(gauge_name, request):
        for _ in range(scan.MAX_UNKNOWN_PER_MINUTE):        # another connection finishes guessing meanwhile
            scan._unknown.add('testclient')
        return real(gauge_name, request)

    monkeypatch.setattr(scan, '_find_gauge', _slow_lookup)
    assert client.get('/scan/0096').status_code == 429
    scan.reset_rate_limits()
    assert client.get('/scan/0096/qr.png').status_code == 429


def test_the_allowance_is_enforced_again_in_the_next_minute(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, 'monotonic', lambda: now[0])
    scan.reset_rate_limits()
    for minute in range(3):
        statuses = [client.get(f'/scan/guess-{minute}-{i}').status_code
                    for i in range(scan.MAX_UNKNOWN_PER_MINUTE + 2)]
        assert statuses.count(404) == scan.MAX_UNKNOWN_PER_MINUTE, f'minute {minute}'
        now[0] += 61


def test_a_refusal_says_when_to_come_back(client):
    for i in range(scan.MAX_UNKNOWN_PER_MINUTE):
        client.get(f'/scan/guess-{i}')
    assert client.get('/scan/0096').headers['retry-after'] == '60'


def _add_gauge(client_db, lab, name):
    conn = get_conn(client_db)
    conn.execute(
        "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, unit, location) "
        "VALUES (?,?,?,?,?,?)", [lab, name, '2026-01-01T00:00:00Z', 1.0, 'Pa', 'Somewhere'])
    conn.commit()
    conn.close()


def test_the_qr_image_and_the_page_agree_on_which_gauges_exist(client):
    """A gauge that belongs to another lab must be unknown on BOTH routes -- the
    image route used to say it existed."""
    import os
    _add_gauge(os.environ['DATABASE_PATH'], 'some-other-lab', 'X-900')
    assert client.get('/scan/X-900').status_code == 404
    assert client.get('/scan/X-900/qr.png').status_code == 404


def test_loading_the_index_never_counts_as_guessing(client):
    """Every gauge the logged-in index lists must have a working QR image, or just
    opening the page would use up the viewer's guessing allowance."""
    import os
    import re
    from api.auth import get_current_user
    for i in range(scan.MAX_UNKNOWN_PER_MINUTE + 5):
        _add_gauge(os.environ['DATABASE_PATH'], 'some-other-lab', f'OTHER-{i}')
    prev = main.app.dependency_overrides.get(get_current_user)
    main.app.dependency_overrides[get_current_user] = lambda: {'username': 't', 'lab_id': _LAB, 'role': 'viewer'}
    try:
        page = client.get('/scan').text
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        if prev is not None:
            main.app.dependency_overrides[get_current_user] = prev
    images = re.findall(r'src="(/scan/[^"]+/qr\.png)"', page)
    assert images == ['/scan/0096/qr.png']                       # only this lab's gauges are listed
    assert {client.get(src).status_code for src in images} == {200}


# ── who a request is counted against ─────────────────────────────────────────

class _Req:
    def __init__(self, host, forwarded=()):
        from starlette.datastructures import Headers
        self.client = type('C', (), {'host': host})()
        self.headers = Headers(raw=[(b'x-forwarded-for', v.encode()) for v in forwarded])


def test_trusted_proxy_uses_the_last_address_even_across_several_header_lines(monkeypatch):
    """A proxy may add its own X-Forwarded-For LINE rather than append to the caller's;
    the caller's line must not win."""
    monkeypatch.setenv('TRUST_FORWARDED_FOR', '1')
    assert ratelimit.client_key(_Req('10.0.0.1', ['6.6.6.6', '203.0.113.7'])) == '203.0.113.7'
    assert ratelimit.client_key(_Req('10.0.0.1', ['6.6.6.6, 198.51.100.4'])) == '198.51.100.4'


def test_one_ipv6_network_is_one_caller(monkeypatch):
    """Anyone with an IPv6 connection has billions of addresses in their own /64."""
    monkeypatch.setenv('TRUST_FORWARDED_FOR', '1')
    keys = {ratelimit.client_key(_Req('10.0.0.1', [f'2001:db8:1:2::{i:x}'])) for i in range(1, 30)}
    assert len(keys) == 1
    assert ratelimit.client_key(_Req('10.0.0.1', ['2001:db8:1:3::1'])) not in keys


def test_port_numbers_and_oversized_values_do_not_make_new_callers(monkeypatch):
    monkeypatch.setenv('TRUST_FORWARDED_FOR', '1')
    assert ratelimit.client_key(_Req('10.0.0.1', ['203.0.113.7:51000'])) == '203.0.113.7'
    assert ratelimit.client_key(_Req('10.0.0.1', ['[2001:db8::1]:443'])) == ratelimit.client_key(_Req('x', ['2001:db8::2']))
    assert len(ratelimit.client_key(_Req('10.0.0.1', ['z' * 60000]))) <= 64
