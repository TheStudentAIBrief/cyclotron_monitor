"""Role-based access control + least privilege (external security review recommendation).

Three roles, each a superset of the one before:
    viewer   -- read-only (dashboards, records, gauge history)
    operator -- viewer + submit gauge readings
    admin    -- operator + bulk import, delete regulated records, manage users

The pre-existing single login (data/.credentials.json) stays as an admin
"break-glass" account so a deploy of this change can never lock the lab out.
Every test here goes through the real auth path (no get_current_user bypass):
the role is looked up from the user store on every request, so a demotion or
a disabled account takes effect immediately rather than at token expiry.
"""
import json

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api import auth
from api import config as _config
from api.auth import create_tokens, get_current_user
from api.db_cloud import get_conn, init_cloud_tables

_LAB = 'petlabs-pretoria'
_ADMIN = 'root-admin'
_ADMIN_PW = 'bootstrap-password-123'
_PW = 'a-long-user-password'


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    # Other test modules leave a get_current_user bypass installed on the shared
    # app -- remove it so these tests exercise real auth, then put it back.
    prev_override = main.app.dependency_overrides.pop(get_current_user, None)
    path = str(tmp_path / 'petlab.db')
    monkeypatch.setenv('DATABASE_PATH', path)
    monkeypatch.setenv('LAB_ID', _LAB)
    monkeypatch.setenv('BOOTSTRAP_USERNAME', _ADMIN)
    monkeypatch.setenv('BOOTSTRAP_PASSWORD', _ADMIN_PW)
    monkeypatch.delenv('BOOTSTRAP_FORCE_RESET', raising=False)
    _config.get_config.cache_clear()
    init_cloud_tables(path)
    auth.ensure_bootstrap_credentials(path)
    main._login_rate_counts.clear()
    main._login_username_rate_counts.clear()
    try:
        yield path
    finally:
        _config.get_config.cache_clear()
        main._login_rate_counts.clear()
        main._login_username_rate_counts.clear()
        if prev_override is not None:
            main.app.dependency_overrides[get_current_user] = prev_override


def _hdr(username: str) -> dict:
    return {'Authorization': f"Bearer {create_tokens(username, _LAB)['access_token']}"}


def _make_user(client, username: str, role: str, password: str = _PW):
    r = client.post('/api/admin/users', headers=_hdr(_ADMIN),
                    json={'username': username, 'password': password, 'role': role})
    assert r.status_code == 201, r.text
    return r


def _seed_reading(db_path: str, reading_id: int = 7001) -> int:
    conn = get_conn(db_path)
    conn.execute(
        "INSERT INTO gauge_readings (id, lab_id, gauge_name, timestamp, value, unit) "
        "VALUES (?,?,?,?,?,?)",
        [reading_id, _LAB, '0096', '2026-01-01T00:00:00Z', 12.3, 'Pa'],
    )
    conn.commit()
    conn.close()
    return reading_id


def _reading_exists(db_path: str, reading_id: int) -> bool:
    conn = get_conn(db_path)
    n = conn.execute("SELECT COUNT(*) FROM gauge_readings WHERE id=?", [reading_id]).fetchone()[0]
    conn.close()
    return n == 1


_MANUAL_READING = {'gauge_name': '0096', 'value': 12.0, 'unit': 'Pa',
                   'is_alert': False, 'alert_reason': ''}


# ── viewer: read-only ─────────────────────────────────────────────────────────

def test_viewer_can_read_gauges_and_records(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'vera', 'viewer')
        assert c.get('/api/gauges', headers=_hdr('vera')).status_code == 200
        assert c.get('/api/records/maintenance', headers=_hdr('vera')).status_code == 200


def test_viewer_cannot_submit_a_gauge_reading(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'vera', 'viewer')
        r = c.post('/api/gauges', headers=_hdr('vera'), json=_MANUAL_READING)
    assert r.status_code == 403
    conn = get_conn(db_path)
    n = conn.execute("SELECT COUNT(*) FROM gauge_readings").fetchone()[0]
    conn.close()
    assert n == 0   # nothing was written


def test_viewer_cannot_submit_a_gauge_photo(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'vera', 'viewer')
        r = c.post('/api/gauges/reading', headers=_hdr('vera'),
                   json={'photo_b64': 'aGVsbG8=', 'gauge_name': '0096'})
    assert r.status_code == 403


# ── operator: viewer + submit readings ────────────────────────────────────────

def test_operator_can_submit_a_gauge_reading(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.post('/api/gauges', headers=_hdr('oscar'), json=_MANUAL_READING)
    assert r.status_code == 200, r.text


def test_operator_cannot_delete_a_regulated_record(db_path):
    rid = _seed_reading(db_path)
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.delete(f'/api/gauges/{rid}', headers=_hdr('oscar'))
    assert r.status_code == 403
    assert _reading_exists(db_path, rid)


@pytest.mark.parametrize('method,path,kwargs', [
    ('post', '/api/admin/import/beam-daily', {'json': {'rows': []}}),
    ('post', '/api/admin/import/gauge-readings', {'json': {'rows': []}}),
    ('post', '/api/gauges/import-csv',
     {'files': {'file': ('g.csv', b'gauge,location,date,value_Pa\n0096,Lab,2026-01-01,12\n', 'text/csv')}}),
    ('post', '/api/gauges/eur-photos', {'json': {'photos_b64': []}}),
    ('get', '/api/admin/users', {}),
    ('post', '/api/admin/users', {'json': {'username': 'eve', 'password': _PW, 'role': 'admin'}}),
    ('post', '/api/admin/users/oscar', {'json': {'role': 'admin'}}),
])
def test_operator_is_refused_every_admin_only_route(db_path, method, path, kwargs):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = getattr(c, method)(path, headers=_hdr('oscar'), **kwargs)
    assert r.status_code == 403, f'{method.upper()} {path} -> {r.status_code}'


def test_operator_cannot_promote_themselves(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        c.post('/api/admin/users/oscar', headers=_hdr('oscar'), json={'role': 'admin'})
        users = c.get('/api/admin/users', headers=_hdr(_ADMIN)).json()
    assert {u['username']: u['role'] for u in users}['oscar'] == 'operator'


# ── admin ─────────────────────────────────────────────────────────────────────

def test_existing_single_login_keeps_full_admin_access(db_path):
    """Deploying RBAC must not lock out the one account the lab already uses."""
    rid = _seed_reading(db_path)
    with TestClient(main.app) as c:
        login = c.post('/auth/login', data={'username': _ADMIN, 'password': _ADMIN_PW})
        assert login.status_code == 200
        assert login.json()['role'] == 'admin'
        hdr = {'Authorization': f"Bearer {login.json()['access_token']}"}
        assert c.post('/api/admin/import/beam-daily', headers=hdr, json={'rows': []}).status_code == 200
        assert c.delete(f'/api/gauges/{rid}', headers=hdr).status_code == 200


def test_delete_audit_records_the_real_account_name(db_path):
    """The audit actor comes from the token's subject -- with a real token (not a
    test bypass) it used to be recorded as an empty string."""
    rid = _seed_reading(db_path)
    with TestClient(main.app) as c:
        _make_user(c, 'alice', 'admin')
        assert c.delete(f'/api/gauges/{rid}', headers=_hdr('alice')).status_code == 200
    conn = get_conn(db_path)
    actor = conn.execute(
        "SELECT actor FROM audit_log WHERE action='delete_gauge_reading' ORDER BY id DESC"
    ).fetchone()['actor']
    conn.close()
    assert actor == 'alice'


# ── user management ───────────────────────────────────────────────────────────

def test_created_user_can_log_in_and_gets_their_role(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.post('/auth/login', data={'username': 'oscar', 'password': _PW})
        assert r.status_code == 200
        assert r.json()['role'] == 'operator'
        bad = c.post('/auth/login', data={'username': 'oscar', 'password': 'wrong-password-xx'})
        assert bad.status_code == 401


def test_user_list_never_exposes_password_hashes(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.get('/api/admin/users', headers=_hdr(_ADMIN))
    assert r.status_code == 200
    assert [u['username'] for u in r.json()] == ['oscar']
    assert set(r.json()[0]) == {'username', 'role', 'disabled', 'created_at', 'created_by'}
    assert r.json()[0]['created_by'] == _ADMIN


@pytest.mark.parametrize('body,status', [
    ({'username': 'bob', 'password': 'short', 'role': 'viewer'}, 400),          # weak password
    ({'username': 'bob', 'password': _PW, 'role': 'superuser'}, 400),           # unknown role
    ({'username': 'bad name!', 'password': _PW, 'role': 'viewer'}, 400),        # bad username
    ({'username': _ADMIN, 'password': _PW, 'role': 'viewer'}, 409),             # shadows built-in admin
])
def test_create_user_rejects_invalid_requests(db_path, body, status):
    with TestClient(main.app) as c:
        r = c.post('/api/admin/users', headers=_hdr(_ADMIN), json=body)
        listed = c.get('/api/admin/users', headers=_hdr(_ADMIN)).json()
    assert r.status_code == status, r.text
    assert listed == []


def test_create_user_rejects_duplicate_username(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.post('/api/admin/users', headers=_hdr(_ADMIN),
                   json={'username': 'oscar', 'password': _PW, 'role': 'admin'})
        users = c.get('/api/admin/users', headers=_hdr(_ADMIN)).json()
    assert r.status_code == 409
    assert users[0]['role'] == 'operator'   # the existing account was not overwritten


def test_update_unknown_user_is_404(db_path):
    with TestClient(main.app) as c:
        r = c.post('/api/admin/users/nobody', headers=_hdr(_ADMIN), json={'role': 'viewer'})
    assert r.status_code == 404


def test_demotion_takes_effect_on_an_already_issued_token(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        token = _hdr('oscar')   # issued while still an operator
        assert c.post('/api/gauges', headers=token, json=_MANUAL_READING).status_code == 200
        r = c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'role': 'viewer'})
        assert r.status_code == 200
        assert c.post('/api/gauges', headers=token, json=_MANUAL_READING).status_code == 403


def test_disabled_user_is_locked_out_immediately(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        toks = create_tokens('oscar', _LAB)
        access = {'Authorization': f"Bearer {toks['access_token']}"}
        assert c.get('/api/gauges', headers=access).status_code == 200

        r = c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'disabled': True})
        assert r.status_code == 200

        assert c.get('/api/gauges', headers=access).status_code == 401          # live token dead
        refresh = c.post('/auth/refresh', headers={'Authorization': f"Bearer {toks['refresh_token']}"})
        assert refresh.status_code == 401                                        # can't mint a new one
        login = c.post('/auth/login', data={'username': 'oscar', 'password': _PW})
        assert login.status_code == 401                                          # can't log back in


def test_admin_can_reset_a_password(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN),
                   json={'password': 'a-brand-new-password'})
        assert r.status_code == 200
        assert c.post('/auth/login', data={'username': 'oscar', 'password': _PW}).status_code == 401
        assert c.post('/auth/login',
                      data={'username': 'oscar', 'password': 'a-brand-new-password'}).status_code == 200


def test_token_for_an_account_that_does_not_exist_is_rejected(db_path):
    with TestClient(main.app) as c:
        r = c.get('/api/gauges', headers=_hdr('ghost'))
    assert r.status_code == 401


def test_user_management_is_audited_without_leaking_the_password(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'role': 'admin'})
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT action, actor, detail FROM audit_log WHERE action LIKE 'user_%' ORDER BY id"
    ).fetchall()
    conn.close()
    assert [r['action'] for r in rows] == ['user_create', 'user_update']
    assert all(r['actor'] == _ADMIN for r in rows)
    assert 'oscar' in rows[0]['detail'] and 'operator' in rows[0]['detail']
    assert all(_PW not in r['detail'] for r in rows)


# ── gaps found by independent review ──────────────────────────────────────────

def _tokens(username: str) -> tuple[dict, dict]:
    toks = create_tokens(username, _LAB)
    return ({'Authorization': f"Bearer {toks['access_token']}"},
            {'Authorization': f"Bearer {toks['refresh_token']}"})


def test_password_reset_kills_tokens_issued_before_it(db_path):
    """Resetting a password is the response to a suspected stolen login -- whoever
    holds the old tokens must be evicted, not left with 7 days of refresh."""
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        access, refresh = _tokens('oscar')
        assert c.get('/api/gauges', headers=access).status_code == 200
        c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'password': 'a-brand-new-password'})
        assert c.get('/api/gauges', headers=access).status_code == 401
        assert c.post('/auth/refresh', headers=refresh).status_code == 401
        # a fresh login after the reset works normally
        login = c.post('/auth/login', data={'username': 'oscar', 'password': 'a-brand-new-password'})
        new = {'Authorization': f"Bearer {login.json()['access_token']}"}
        assert c.get('/api/gauges', headers=new).status_code == 200


def test_re_enabling_an_account_does_not_revive_its_old_tokens(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        access, refresh = _tokens('oscar')
        c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'disabled': True})
        r = c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'disabled': False})
        assert r.status_code == 200
        assert c.get('/api/gauges', headers=access).status_code == 401
        assert c.post('/auth/refresh', headers=refresh).status_code == 401
        # ...but the account itself is usable again
        assert c.post('/auth/login', data={'username': 'oscar', 'password': _PW}).status_code == 200


def test_role_change_alone_keeps_the_session(db_path):
    """Only a reset or a disable/enable evicts sessions; a promotion must not log people out."""
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        access, _ = _tokens('oscar')
        c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'role': 'admin'})
        assert c.get('/api/admin/users', headers=access).status_code == 200


def test_renaming_the_builtin_admin_onto_a_user_does_not_promote_their_token(db_path, tmp_path):
    with TestClient(main.app) as c:
        _make_user(c, 'vera', 'viewer')
        access, _ = _tokens('vera')
        creds = tmp_path / '.credentials.json'
        data = json.loads(creds.read_text())
        creds.write_text(json.dumps({**data, 'username': 'vera'}))   # as BOOTSTRAP_FORCE_RESET would
        r = c.get('/api/admin/users', headers=access)
    assert r.status_code == 401


@pytest.mark.parametrize('body', [
    {},                                   # nothing to change
    {'disable': True},                    # mistyped field -- must not look like it worked
    {'role': 'superuser'},
    {'password': 'short'},
])
def test_update_rejects_requests_that_change_nothing_or_are_invalid(db_path, body):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json=body)
        user = c.get('/api/admin/users', headers=_hdr(_ADMIN)).json()[0]
        still_logs_in = c.post('/auth/login', data={'username': 'oscar', 'password': _PW}).status_code
    assert r.status_code in (400, 422), r.text
    assert (user['role'], user['disabled'], still_logs_in) == ('operator', False, 200)
    conn = get_conn(db_path)
    n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='user_update'").fetchone()[0]
    conn.close()
    assert n == 0   # no audit row claiming a change that never happened


def test_validation_errors_never_echo_the_password_back(db_path):
    with TestClient(main.app) as c:
        r = c.post('/api/admin/users', headers=_hdr(_ADMIN),
                   json={'username': 'bob', 'password': 'super-secret-password'})   # role missing
    assert r.status_code == 422
    assert 'super-secret-password' not in r.text


def test_password_reset_audit_never_contains_the_password_or_hash(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.post('/api/admin/users/oscar', headers=_hdr(_ADMIN), json={'password': 'a-brand-new-password'})
    conn = get_conn(db_path)
    details = [row['detail'] for row in conn.execute("SELECT detail FROM audit_log WHERE action LIKE 'user_%'")]
    stored_hash = conn.execute("SELECT password_hash FROM users WHERE username='oscar'").fetchone()[0]
    conn.close()
    leaked = [s for s in ('a-brand-new-password', _PW, stored_hash) if any(s in d for d in details) or s in r.text]
    assert leaked == []
    assert 'password_reset' in details[-1]


@pytest.mark.parametrize('username', ['Root-Admin', 'ROOT-ADMIN', 'Oscar'])
def test_usernames_that_differ_only_by_case_are_refused(db_path, username):
    """Look-alike names (Root-Admin vs root-admin) make the audit log ambiguous."""
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        r = c.post('/api/admin/users', headers=_hdr(_ADMIN),
                   json={'username': username, 'password': _PW, 'role': 'viewer'})
    assert r.status_code in (400, 409), r.text


def test_account_with_an_unrecognised_role_cannot_log_in(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'oscar', 'operator')
        conn = get_conn(db_path)
        conn.execute("UPDATE users SET role='superuser' WHERE username='oscar'")
        conn.commit()
        conn.close()
        r = c.post('/auth/login', data={'username': 'oscar', 'password': _PW})
    assert r.status_code == 401


def test_corrupt_credentials_file_does_not_break_other_accounts(db_path, tmp_path):
    with TestClient(main.app) as c:
        _make_user(c, 'vera', 'viewer')
        access, _ = _tokens('vera')
        (tmp_path / '.credentials.json').write_text('["not", "an", "object"]')
        r = c.get('/api/gauges', headers=access)
    assert r.status_code == 200


def test_viewer_keeps_the_documented_non_read_routes(db_path):
    with TestClient(main.app) as c:
        _make_user(c, 'vera', 'viewer')
        r = c.post('/api/push/register', headers=_hdr('vera'), json={'token': 'ExponentPushToken[x]'})
    assert r.status_code == 200
