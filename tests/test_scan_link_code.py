"""An unguessable code in the QR link.

The rate limit slows someone walking through gauge names; it does not stop them.
With QR_LINK_SECRET set, every QR link the system makes carries a code derived
from the gauge name and that secret. With QR_REQUIRE_CODE=1 as well, a gauge
page only opens for a request carrying the right code -- knowing or guessing a
gauge name is no longer enough. Until then, labels printed without a code keep
working, so the labels can be reprinted first and the switch thrown after.
"""
import pytest

from api.routes import scan
from monitor.gauge_scan import gauge_scan_url, scan_code
from tests.test_rbac import _ADMIN, _hdr, db_path as _rbac_db  # noqa: F401  (fixture)
from tests.test_scan_rate_limit import client  # noqa: F401  (fixture)

_SECRET = 'a-long-random-qr-link-secret'
_CODE = scan_code(_SECRET, '0096')


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv('QR_LINK_SECRET', _SECRET)
    monkeypatch.delenv('QR_REQUIRE_CODE', raising=False)


@pytest.fixture
def required(secret, monkeypatch):
    monkeypatch.setenv('QR_REQUIRE_CODE', '1')


def test_the_code_depends_on_the_gauge_and_the_secret():
    assert len(_CODE) == 20 and _CODE.isalnum()
    assert scan_code(_SECRET, '0097') != _CODE
    assert scan_code('another-secret', '0096') != _CODE
    assert gauge_scan_url('https://x.test/', '0096', _CODE) == f'https://x.test/scan/0096?c={_CODE}'
    assert gauge_scan_url('https://x.test', '0096') == 'https://x.test/scan/0096'


def test_a_gauge_name_with_a_space_or_a_hash_does_not_cut_the_link_short():
    assert gauge_scan_url('https://x.test', 'Gas Flow', 'abc') == 'https://x.test/scan/Gas%20Flow?c=abc'
    assert gauge_scan_url('https://x.test', 'PG#3', 'abc') == 'https://x.test/scan/PG%233?c=abc'
    assert gauge_scan_url('https://x.test', 'A?B/C') == 'https://x.test/scan/A%3FB%2FC'


# ── before the switch: old labels still work ─────────────────────────────────

def test_without_the_switch_a_label_printed_without_a_code_still_works(client, secret):  # noqa: F811
    assert client.get('/scan/0096').status_code == 200
    assert client.get(f'/scan/0096?c={_CODE}').status_code == 200


def test_a_page_opened_without_the_code_never_gives_the_code_away(client, secret):  # noqa: F811
    """Otherwise codes could be collected now and used after the switch is thrown."""
    assert _CODE not in client.get('/scan/0096?format=json').text
    assert _CODE not in client.get('/scan/0096').text


def test_a_page_opened_with_the_code_links_back_to_itself_with_the_code(client, secret):  # noqa: F811
    body = client.get(f'/scan/0096?format=json&c={_CODE}').json()

    assert body['scan_url'].endswith(f'/scan/0096?c={_CODE}')


# ── after the switch: the name alone is not enough ───────────────────────────

def test_with_the_switch_a_real_gauge_name_alone_opens_nothing(client, required):  # noqa: F811
    for url in ('/scan/0096', '/scan/0096?format=json', '/scan/0096/qr.png',
                '/scan/0096?c=', '/scan/0096?c=00000000000000000000',
                f'/scan/0096?c={scan_code(_SECRET, "0097")}'):
        reply = client.get(url)
        assert reply.status_code == 404, url
        assert 'Cleanroom' not in reply.text


def test_with_the_switch_the_right_code_opens_the_page_and_the_image(client, required):  # noqa: F811
    page = client.get(f'/scan/0096?c={_CODE}')
    assert page.status_code == 200 and 'Cleanroom' in page.text
    assert client.get(f'/scan/0096?format=json&c={_CODE}').status_code == 200
    image = client.get(f'/scan/0096/qr.png?c={_CODE}')
    assert image.status_code == 200 and image.headers['content-type'] == 'image/png'


def test_a_real_name_without_the_code_looks_exactly_like_a_name_that_does_not_exist(client, required):  # noqa: F811
    real, made_up = client.get('/scan/0096'), client.get('/scan/9999')

    assert real.status_code == made_up.status_code == 404
    assert real.json()['error'] == made_up.json()['error']


def test_wrong_codes_use_up_the_guessing_allowance(client, required):  # noqa: F811
    statuses = [client.get(f'/scan/0096?c=guess{i}').status_code for i in range(scan.MAX_UNKNOWN_PER_MINUTE + 2)]

    assert statuses[:scan.MAX_UNKNOWN_PER_MINUTE] == [404] * scan.MAX_UNKNOWN_PER_MINUTE
    assert set(statuses[scan.MAX_UNKNOWN_PER_MINUTE:]) == {429}
    assert client.get(f'/scan/0096?c={_CODE}').status_code == 429      # and the right code is refused too


def test_the_switch_without_a_secret_opens_nothing(client, monkeypatch):  # noqa: F811
    monkeypatch.delenv('QR_LINK_SECRET', raising=False)
    monkeypatch.setenv('QR_REQUIRE_CODE', '1')

    assert client.get('/scan/0096').status_code == 404
    assert client.get('/scan/0096?c=').status_code == 404


# ── the logged-in gauge list ─────────────────────────────────────────────────

def _seed(path):
    from api.db_cloud import get_conn
    conn = get_conn(path)
    conn.execute("INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, unit, location) "
                 "VALUES (?,?,?,?,?,?)", ['petlabs-pretoria', '0096', '2026-01-01T00:00:00Z', 12.3, 'Pa', 'Cleanroom'])
    conn.commit()
    conn.close()


def test_the_logged_in_gauge_list_links_with_codes_once_a_secret_is_set(_rbac_db, secret):  # noqa: F811
    from fastapi.testclient import TestClient
    import api.main as main
    _seed(_rbac_db)
    scan.reset_rate_limits()

    with TestClient(main.app) as c:
        page = c.get('/scan', headers=_hdr(_ADMIN)).text

    assert f'href="/scan/0096?c={_CODE}"' in page
    assert f'src="/scan/0096/qr.png?c={_CODE}"' in page


def test_the_logged_in_gauge_list_is_unchanged_without_a_secret(_rbac_db, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    import api.main as main
    monkeypatch.delenv('QR_LINK_SECRET', raising=False)
    _seed(_rbac_db)
    scan.reset_rate_limits()

    with TestClient(main.app) as c:
        page = c.get('/scan', headers=_hdr(_ADMIN)).text

    assert 'href="/scan/0096"' in page and 'src="/scan/0096/qr.png"' in page
