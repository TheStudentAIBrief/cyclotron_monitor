"""Resetting the built-in admin's password (BOOTSTRAP_FORCE_RESET).

The first production password is recoverable from the repository's history, so
a reset has to do two things a plain file rewrite does not: leave a record that
it happened, and end every session opened with the old password.
"""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import audit, auth
from api.db_cloud import get_conn
from tests.test_rbac import _ADMIN, _ADMIN_PW, db_path  # noqa: F401  (fixture)

_NEW_PW = 'a-brand-new-password-456'


def _audit(db_path):
    conn = get_conn(db_path)
    try:
        return [(r['action'], r['actor'], r['detail']) for r in
                conn.execute("SELECT action, actor, detail FROM audit_log ORDER BY id")]
    finally:
        conn.close()


def _login(client, password):
    main._login_rate_counts.clear()
    main._login_username_rate_counts.clear()
    return client.post('/auth/login', data={'username': _ADMIN, 'password': password})


def _force_reset(db_path, monkeypatch, password=_NEW_PW):
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', password)
    monkeypatch.setenv('BOOTSTRAP_FORCE_RESET', 'true')
    auth.ensure_bootstrap_credentials(db_path)
    monkeypatch.delenv('BOOTSTRAP_FORCE_RESET')


def test_creating_the_built_in_login_is_recorded(db_path):
    entries = _audit(db_path)

    assert [e[0] for e in entries] == ['builtin_login_created']
    assert entries[0][1] == 'bootstrap' and _ADMIN in entries[0][2]


def test_a_forced_reset_is_recorded_without_the_password(db_path, monkeypatch):
    _force_reset(db_path, monkeypatch)

    entries = _audit(db_path)
    assert [e[0] for e in entries] == ['builtin_login_created', 'builtin_password_reset']
    assert _NEW_PW not in json.dumps(entries) and _ADMIN_PW not in json.dumps(entries)
    conn = get_conn(db_path)
    assert audit.verify(conn)['ok']
    conn.close()


def test_an_ordinary_restart_records_nothing(db_path):
    auth.ensure_bootstrap_credentials(db_path)
    auth.ensure_bootstrap_credentials(db_path)

    assert [e[0] for e in _audit(db_path)] == ['builtin_login_created']


def test_a_reset_that_was_refused_is_not_recorded_and_keeps_the_old_login(db_path, monkeypatch):
    """It used to delete the login first and check the new password second, locking everyone out."""
    _force_reset(db_path, monkeypatch, password='too-short')

    assert 'builtin_password_reset' not in [e[0] for e in _audit(db_path)]
    with TestClient(main.app) as client:
        assert _login(client, _ADMIN_PW).status_code == 200


def test_a_reset_with_no_password_configured_keeps_the_old_login(db_path, monkeypatch):
    monkeypatch.delenv('BOOTSTRAP_PASSWORD')
    monkeypatch.setenv('BOOTSTRAP_FORCE_RESET', 'true')
    auth.ensure_bootstrap_credentials(db_path)
    monkeypatch.delenv('BOOTSTRAP_FORCE_RESET')

    with TestClient(main.app) as client:
        assert _login(client, _ADMIN_PW).status_code == 200


def test_a_forced_reset_ends_sessions_opened_with_the_old_password(db_path, monkeypatch):
    with TestClient(main.app) as client:
        old = _login(client, _ADMIN_PW).json()
        assert client.get('/api/admin/users',
                          headers={'Authorization': f"Bearer {old['access_token']}"}).status_code == 200

        _force_reset(db_path, monkeypatch)

        assert client.get('/api/admin/users',
                          headers={'Authorization': f"Bearer {old['access_token']}"}).status_code == 401
        client.cookies.clear()
        assert client.post('/auth/refresh',
                           headers={'Authorization': f"Bearer {old['refresh_token']}"}).status_code == 401
        assert _login(client, _ADMIN_PW).status_code == 401
        new = _login(client, _NEW_PW)
        assert new.status_code == 200
        assert client.get('/api/admin/users',
                          headers={'Authorization': f"Bearer {new.json()['access_token']}"}).status_code == 200


def test_a_login_file_from_before_this_change_keeps_its_sessions(db_path):
    """Deploying this must not log the lab out: an existing file has no version, and counts as 0."""
    creds_path = Path(db_path).parent / '.credentials.json'
    creds = json.loads(creds_path.read_text(encoding='utf-8'))
    creds.pop('token_version', None)
    creds_path.write_text(json.dumps(creds), encoding='utf-8')

    with TestClient(main.app) as client:
        tokens = _login(client, _ADMIN_PW).json()
        assert client.get('/api/admin/users',
                          headers={'Authorization': f"Bearer {tokens['access_token']}"}).status_code == 200
    assert auth._account(_ADMIN) == ('admin', 0)


def test_the_built_in_version_can_never_match_a_personal_account(db_path, monkeypatch):
    _force_reset(db_path, monkeypatch)

    assert auth._account(_ADMIN)[1] < 0          # personal accounts start at 1 and only go up


# ── second pass: findings of the independent review ──────────────────────────

def test_a_reset_whose_write_fails_keeps_the_old_login_and_does_not_stop_start_up(db_path, monkeypatch):
    def _disk_full(src, dst):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(auth.os, 'replace', _disk_full)
    _force_reset(db_path, monkeypatch)                    # must not raise
    monkeypatch.undo()

    with TestClient(main.app) as client:
        assert _login(client, _ADMIN_PW).status_code == 200
    assert 'builtin_password_reset' not in [e[0] for e in _audit(db_path)]
    assert [p.name for p in Path(db_path).parent.iterdir() if p.name.startswith('.credentials')] == [
        '.credentials.json']                              # no half-written file left behind


def test_leaving_the_reset_flag_on_does_not_reset_again(db_path, monkeypatch):
    """Otherwise every restart would log the admin out and add an audit entry."""
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', _NEW_PW)
    monkeypatch.setenv('BOOTSTRAP_FORCE_RESET', 'true')
    auth.ensure_bootstrap_credentials(db_path)
    with TestClient(main.app) as client:
        tokens = _login(client, _NEW_PW).json()

        auth.ensure_bootstrap_credentials(db_path)        # a restart with the flag still set
        auth.ensure_bootstrap_credentials(db_path)

        assert client.get('/api/admin/users',
                          headers={'Authorization': f"Bearer {tokens['access_token']}"}).status_code == 200
    assert [e[0] for e in _audit(db_path)] == ['builtin_login_created', 'builtin_password_reset']


def test_a_reset_that_could_not_be_recorded_is_recorded_on_a_later_start(db_path, monkeypatch):
    real_write = audit.write

    def _locked(*args, **kwargs):
        raise sqlite3.OperationalError('database is locked')

    monkeypatch.setattr(audit, 'write', _locked)
    _force_reset(db_path, monkeypatch)
    assert 'builtin_password_reset' not in [e[0] for e in _audit(db_path)]

    monkeypatch.setattr(audit, 'write', real_write)
    auth.ensure_bootstrap_credentials(db_path)            # an ordinary restart
    auth.ensure_bootstrap_credentials(db_path)

    assert [e[0] for e in _audit(db_path)] == ['builtin_login_created', 'builtin_password_reset']
    with TestClient(main.app) as client:
        assert _login(client, _NEW_PW).status_code == 200


@pytest.mark.parametrize('content', [
    '{"username": "root-admin", "hash": "x"}'.encode('utf-16'),
    b'{"username": "root-admin", "hash": "x", "token_version": ' + b'9' * 5000 + b'}',
    b'\xff\xfe\x00garbage',
])
def test_a_login_file_that_cannot_be_read_refuses_logins_instead_of_failing(db_path, content):
    (Path(db_path).parent / '.credentials.json').write_bytes(content)

    with TestClient(main.app) as client:
        assert _login(client, _ADMIN_PW).status_code == 401


def test_the_web_refresh_cookie_stops_working_after_a_reset(db_path, monkeypatch):
    with TestClient(main.app, base_url='https://testserver') as client:     # the cookie is https-only
        assert _login(client, _ADMIN_PW).status_code == 200          # sets the refresh cookie
        assert client.post('/auth/refresh').status_code == 200

        _force_reset(db_path, monkeypatch)

        assert client.post('/auth/refresh').status_code == 401


def test_a_reset_is_recorded_once_even_if_the_login_file_cannot_be_updated_afterwards(db_path, monkeypatch):
    """The entry is written, then the file is marked as recorded. If that second
    step keeps failing, the same reset must not be recorded again on every start."""
    _force_reset(db_path, monkeypatch)
    real_write = auth._write_creds
    creds_path = Path(db_path).parent / '.credentials.json'
    pending = json.loads(creds_path.read_text(encoding='utf-8'))
    pending.update(audit_pending='builtin_password_reset', audit_ref='feedc0de')
    creds_path.write_text(json.dumps(pending), encoding='utf-8')
    before = len(_audit(db_path))

    def _read_only(path, creds):
        raise PermissionError(13, 'Permission denied')

    monkeypatch.setattr(auth, '_write_creds', _read_only)
    for _ in range(3):
        auth.ensure_bootstrap_credentials(db_path)            # three restarts
    monkeypatch.setattr(auth, '_write_creds', real_write)
    auth.ensure_bootstrap_credentials(db_path)

    assert len(_audit(db_path)) == before + 1
    assert 'audit_pending' not in json.loads(creds_path.read_text(encoding='utf-8'))
