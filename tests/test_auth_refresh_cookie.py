"""Web refresh-token cookie (self-pentest F-04 remediation).

api/auth.py's set_refresh_cookie() mirrors the refresh token into an httpOnly,
Secure, SameSite=Strict cookie on login/refresh, so mobile/services/auth.ts can
stop persisting it in web localStorage (see that file's web-specific branches).
Native clients are unaffected: they never receive/send this cookie and keep
using the JSON body's refresh_token via SecureStore exactly as before.

Uses monkeypatch.setenv (auto-reverted per test), not os.environ.setdefault at
module level -- see test_admin_import.py's docstring for why a bare setdefault
here would leak DATABASE_PATH into unrelated test files collected afterward.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import config as _config
from api.db_cloud import init_cloud_tables

_PASSWORD = 'a-long-enough-test-password'


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    # ensure_bootstrap_credentials() writes .credentials.json next to db_path
    # (Path(db_path).parent) and never overwrites an existing one -- a shared
    # parent directory (e.g. the bare system temp root) would let an unrelated
    # test file's credentials file silently shadow this one. tmp_path is a
    # fresh directory per test, so this can never collide.
    path = str(tmp_path / f'petlab_refresh_cookie_{uuid.uuid4().hex}.db')
    monkeypatch.setenv('DATABASE_PATH', path)
    monkeypatch.setenv('BOOTSTRAP_USERNAME', 'tester')
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', _PASSWORD)
    _config.get_config.cache_clear()
    init_cloud_tables(path)
    yield path
    _config.get_config.cache_clear()


def _login(client):
    r = client.post('/auth/login', data={'username': 'tester', 'password': _PASSWORD})
    assert r.status_code == 200
    return r


def test_login_sets_httponly_refresh_cookie(fresh_db):
    with TestClient(main.app) as client:
        r = _login(client)
    set_cookie = r.headers.get('set-cookie', '')
    assert 'refresh_token=' in set_cookie
    assert 'HttpOnly' in set_cookie
    assert 'samesite=strict' in set_cookie.lower()
    assert 'Secure' in set_cookie
    assert 'Path=/auth' in set_cookie


def test_refresh_works_via_cookie_alone_no_authorization_header(fresh_db):
    # A compliant cookie jar (this is the same rule real browsers follow) never
    # attaches a Secure cookie to a plain-http request, so this round trip needs
    # an https base_url to faithfully simulate production (Render is always
    # https) rather than weakening the Secure flag under test.
    with TestClient(main.app, base_url='https://testserver') as client:
        _login(client)  # cookie now lives in the client's jar
        r = client.post('/auth/refresh')  # no Authorization header at all
    assert r.status_code == 200
    body = r.json()
    assert body['access_token']
    assert body['refresh_token']


def test_refresh_still_works_via_authorization_header_only(fresh_db):
    """Backward compatibility: native clients never see the cookie and keep
    sending the refresh token as a Bearer header, exactly as before this change."""
    with TestClient(main.app) as client:
        login_resp = _login(client)
        refresh_tok = login_resp.json()['refresh_token']
        client.cookies.clear()  # simulate a native client that never got a cookie
        r = client.post('/auth/refresh', headers={'Authorization': f'Bearer {refresh_tok}'})
    assert r.status_code == 200
    assert r.json()['access_token']


def test_refresh_without_header_or_cookie_is_401(fresh_db):
    with TestClient(main.app) as client:
        r = client.post('/auth/refresh')
    assert r.status_code == 401


def test_refresh_rotates_the_cookie(fresh_db):
    with TestClient(main.app, base_url='https://testserver') as client:
        _login(client)
        r = client.post('/auth/refresh')
    assert 'refresh_token=' in r.headers.get('set-cookie', '')


def test_logout_clears_the_refresh_cookie(fresh_db):
    with TestClient(main.app) as client:
        login_resp = _login(client)
        access_tok = login_resp.json()['access_token']
        r = client.post('/auth/logout', headers={'Authorization': f'Bearer {access_tok}'})
    assert r.status_code == 200
    set_cookie = r.headers.get('set-cookie', '')
    assert 'refresh_token=' in set_cookie
    # A cleared cookie is re-set with an empty value and an immediate expiry,
    # not a real token -- assert the token that just worked is gone from it.
    assert access_tok not in set_cookie
    assert login_resp.json()['refresh_token'] not in set_cookie


def test_cookie_secure_flag_respects_cookie_secure_env_var(fresh_db, monkeypatch):
    """Local dev runs over plain http, where a Secure cookie is silently
    dropped by the browser -- COOKIE_SECURE=false must be honored for that case."""
    import api.auth as auth
    monkeypatch.setattr(auth, '_COOKIE_SECURE', False)
    with TestClient(main.app) as client:
        r = _login(client)
    assert 'Secure' not in r.headers.get('set-cookie', '')
