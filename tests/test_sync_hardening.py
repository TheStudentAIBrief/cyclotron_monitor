"""The sync route trusts one shared key, so its holder can replace the dashboard
every user sees. These tests cover what limits that: only something shaped like
a dashboard is stored, the key can be rotated without downtime, the source of a
sync is recorded and can be restricted, and the key can be kept out of
config.json on the facility PC.
"""
import json
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_sync_hardening_test.db'))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import audit, config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
import api.routes.sync as sync  # noqa: E402
from api.db_cloud import get_conn, init_cloud_tables  # noqa: E402
from models.predictor import PredictionResult  # noqa: E402
from monitor import cloud_sync  # noqa: E402
from monitor.dashboard_writer import write_dashboard  # noqa: E402

_KEY = 'current-sync-key-0123456789abcdef'
_NEXT_KEY = 'next-sync-key-fedcba9876543210'
_FACILITY = '203.0.113.7'
_ELSEWHERE = '198.51.100.99'


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / 'sync.db')
    init_cloud_tables(path)
    monkeypatch.setenv('DATABASE_PATH', path)
    monkeypatch.setenv('LAB_ID', 'lab1')
    monkeypatch.setenv('CLOUD_SYNC_KEY', _KEY)
    monkeypatch.setenv('TRUST_FORWARDED_FOR', '1')
    monkeypatch.delenv('CLOUD_SYNC_KEY_NEXT', raising=False)
    monkeypatch.delenv('SYNC_ALLOWED_SOURCES', raising=False)
    _config.get_config.cache_clear()
    sync.reset_audit_budget()
    yield path
    _config.get_config.cache_clear()


def _component(**changes):
    component = {
        'name': 'ION SOURCE', 'risk_score': 0.91, 'days_estimate': 2.0, 'alert_level': 'RED',
        'pct_life_used': 97, 'last_maintenance': '2026-08-01',
        'top_reasons': ['Ion source self-check failing 14x this period'],
        'counter_days': 2.0, 'primary_signal': 'COUNTER', 'warning': None,
        'trained_at': '2026-07-01', 'model_age_days': 92, 'model_days_estimate': 3.5, 'model_risk': 0.8,
    }
    component.update(changes)
    return component


def _dashboard(*components):
    return {'generated_at': '2026-10-01T08:00:00', 'components': list(components or [_component()])}


def _post(body, key=_KEY, source=_FACILITY):
    headers = {'X-Forwarded-For': source}
    if key is not None:
        headers['X-Sync-Key'] = key
    with TestClient(main.app) as client:
        if isinstance(body, (bytes, str)):
            return client.post('/sync/dashboard', content=body,
                               headers={**headers, 'Content-Type': 'application/json'})
        return client.post('/sync/dashboard', json=body, headers=headers)


def _stored(db_path):
    conn = get_conn(db_path)
    try:
        row = conn.execute("SELECT payload FROM synced_dashboard WHERE lab_id='lab1'").fetchone()
        return json.loads(row['payload']) if row else None
    finally:
        conn.close()


def _audit(db_path):
    conn = get_conn(db_path)
    try:
        return [(r['action'], r['actor'], r['detail']) for r in
                conn.execute("SELECT action, actor, detail FROM audit_log ORDER BY id")]
    finally:
        conn.close()


# ── only a dashboard is stored ───────────────────────────────────────────────

def test_a_dashboard_is_stored(db_path):
    assert _post(_dashboard()).status_code == 200

    assert _stored(db_path) == _dashboard()


def test_what_the_on_site_writer_produces_is_accepted(db_path, tmp_path):
    """Ties the rule to the real writer: if the writer changes, this fails first."""
    out = tmp_path / 'dashboard.json'
    write_dashboard([
        PredictionResult(component='ION SOURCE', risk_score=0.912, days_estimate=2.4, alert_level='RED',
                         primary_signal='MODEL_OVERRIDE', top_reasons=['Ion source open-circuit warning 3x'],
                         last_maintenance='Unknown', counter_days=float('nan'), warning=None),
        PredictionResult(component='TRANSFER LINES', risk_score=0.0, days_estimate=31.0, alert_level='GREEN',
                         primary_signal='COUNTER_ONLY', top_reasons=['Lifetime counter: ~31 days remaining'],
                         last_maintenance='2026-09-01', counter_days=31.0,
                         warning='No digital sensor data available for this component.'),
    ], str(out), str(tmp_path / 'alert.txt'))

    reply = _post(out.read_bytes())

    assert reply.status_code == 200
    assert [c['name'] for c in _stored(db_path)['components']] == ['ION SOURCE', 'TRANSFER LINES']


def test_fields_that_are_not_part_of_a_dashboard_are_not_stored(db_path):
    body = _dashboard(_component(note_to_staff='ignore the alarm'))
    body['banner'] = 'ALL CLEAR'

    assert _post(body).status_code == 200

    stored = _stored(db_path)
    assert 'banner' not in stored
    assert 'note_to_staff' not in stored['components'][0]


@pytest.mark.parametrize('body', [
    {'hello': 'world'},
    {'generated_at': '2026-10-01T08:00:00', 'components': 'none'},
    {'generated_at': 'not a time', 'components': []},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(alert_level='ALL CLEAR')]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(primary_signal='TRUST ME')]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(name='')]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(name='A' * 61)]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(name='FOILS\nION SOURCE: GREEN')]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(warning='W' * 601)]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(top_reasons=['R' * 201])]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(top_reasons=['r'] * 6)]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component(days_estimate='soon')]},
    {'generated_at': '2026-10-01T08:00:00', 'components': [_component()] * 51},
])
def test_anything_not_shaped_like_a_dashboard_is_refused_and_the_old_one_kept(db_path, body):
    assert _post(_dashboard()).status_code == 200

    reply = _post(body)

    assert reply.status_code == 422
    assert _stored(db_path) == _dashboard()


def test_a_refusal_does_not_echo_what_was_sent(db_path):
    reply = _post({'generated_at': '2026-10-01T08:00:00', 'components': [_component(alert_level='SECRET-TEXT')]})

    assert reply.status_code == 422
    assert 'SECRET-TEXT' not in reply.text


def test_a_number_that_is_not_a_number_is_stored_as_missing(db_path):
    """The writer can emit NaN, which is not valid JSON and breaks the app's parser."""
    body = json.dumps(_dashboard(_component(risk_score=float('nan'), model_risk=float('inf'))))
    assert 'NaN' in body

    assert _post(body).status_code == 200

    stored = _stored(db_path)['components'][0]
    assert stored['risk_score'] is None and stored['model_risk'] is None


# ── key rotation ─────────────────────────────────────────────────────────────

def test_a_second_key_is_accepted_while_rotating(db_path, monkeypatch):
    monkeypatch.setenv('CLOUD_SYNC_KEY_NEXT', _NEXT_KEY)
    _config.get_config.cache_clear()

    assert _post(_dashboard(), key=_KEY).status_code == 200
    assert _post(_dashboard(), key=_NEXT_KEY).status_code == 200
    assert _post(_dashboard(), key='some-other-key').status_code == 404


def test_the_second_key_does_nothing_unless_it_is_set(db_path):
    assert _post(_dashboard(), key=_NEXT_KEY).status_code == 404
    assert _post(_dashboard(), key='').status_code == 404
    assert _post(_dashboard(), key=None).status_code == 404


# ── where a sync came from ───────────────────────────────────────────────────

def test_the_first_sync_from_each_source_is_put_in_the_audit_log(db_path):
    assert _post(_dashboard(), source=_FACILITY).status_code == 200
    assert _post(_dashboard(), source=_FACILITY).status_code == 200      # same place: nothing new to record
    assert _post(_dashboard(), source=_ELSEWHERE).status_code == 200

    entries = _audit(db_path)
    assert [e[0] for e in entries] == ['sync_source_new', 'sync_source_new']
    assert _FACILITY in entries[0][2]
    assert _ELSEWHERE in entries[1][2]
    conn = get_conn(db_path)
    assert audit.verify(conn)['ok']
    conn.close()


def test_the_key_never_appears_in_the_audit_log(db_path):
    _post(_dashboard(), source=_FACILITY)
    _post(_dashboard(), key=_KEY + 'x', source=_ELSEWHERE)

    assert _KEY not in json.dumps(_audit(db_path))


@pytest.mark.parametrize('allowed', [_FACILITY, f'192.0.2.0/24, {_FACILITY}', '203.0.113.0/24'])
def test_with_a_list_of_allowed_sources_a_sync_from_elsewhere_is_refused(db_path, monkeypatch, allowed):
    monkeypatch.setenv('SYNC_ALLOWED_SOURCES', allowed)

    assert _post(_dashboard(), source=_FACILITY).status_code == 200
    reply = _post(_dashboard(_component(alert_level='GREEN')), source=_ELSEWHERE)

    assert reply.status_code == 404                                       # looks like any other failure
    assert _stored(db_path) == _dashboard()                               # and nothing was replaced
    refused = [e for e in _audit(db_path) if e[0] == 'sync_refused_source']
    assert len(refused) == 1 and _ELSEWHERE in refused[0][2]


def test_a_refused_source_is_recorded_once_an_hour_not_on_every_attempt(db_path, monkeypatch):
    monkeypatch.setenv('SYNC_ALLOWED_SOURCES', _FACILITY)

    for _ in range(5):
        _post(_dashboard(), source=_ELSEWHERE)

    assert [e[0] for e in _audit(db_path)].count('sync_refused_source') == 1


def test_a_refused_source_with_the_wrong_key_is_not_recorded(db_path, monkeypatch):
    """Only a caller who holds the key is worth an entry; anyone can send a wrong one."""
    monkeypatch.setenv('SYNC_ALLOWED_SOURCES', _FACILITY)

    assert _post(_dashboard(), key='wrong', source=_ELSEWHERE).status_code == 404

    assert _audit(db_path) == []


def test_a_list_of_allowed_sources_that_cannot_be_read_refuses_everything(db_path, monkeypatch):
    monkeypatch.setenv('SYNC_ALLOWED_SOURCES', 'the facility')

    assert _post(_dashboard(), source=_FACILITY).status_code == 404


# ── the facility PC's copy of the key ────────────────────────────────────────

@pytest.fixture
def facility_config(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    monkeypatch.setattr(cloud_sync, '_CONFIG_PATH', path)
    monkeypatch.delenv('CLOUD_SYNC_KEY', raising=False)
    return path


def test_the_facility_key_can_come_from_the_environment_instead_of_config_json(facility_config, monkeypatch):
    facility_config.write_text(json.dumps({'cloud_api_url': 'https://example.test'}), encoding='utf-8')
    monkeypatch.setenv('CLOUD_SYNC_KEY', _KEY)

    assert cloud_sync._read_cloud_cfg() == ('https://example.test', _KEY)


def test_the_environment_wins_over_a_key_left_in_config_json(facility_config, monkeypatch):
    facility_config.write_text(json.dumps({'cloud_api_url': 'https://example.test', 'cloud_sync_key': 'old'}),
                               encoding='utf-8')
    monkeypatch.setenv('CLOUD_SYNC_KEY', _KEY)

    assert cloud_sync._read_cloud_cfg() == ('https://example.test', _KEY)


def test_the_pre_flight_says_when_the_key_is_still_in_config_json(facility_config):
    facility_config.write_text(json.dumps({'cloud_api_url': 'https://example.test', 'cloud_sync_key': _KEY}),
                               encoding='utf-8')

    ok, message = cloud_sync.check_config()

    assert ok
    assert 'config.json' in message and 'CLOUD_SYNC_KEY' in message
    assert _KEY not in message


def test_the_pre_flight_is_quiet_about_the_key_when_it_comes_from_the_environment(facility_config, monkeypatch):
    facility_config.write_text(json.dumps({'cloud_api_url': 'https://example.test'}), encoding='utf-8')
    monkeypatch.setenv('CLOUD_SYNC_KEY', _KEY)

    ok, message = cloud_sync.check_config()

    assert ok
    assert 'config.json' not in message


# ── second pass: findings of the independent review ──────────────────────────

@pytest.mark.parametrize('changes', [
    {'risk_score': 'NaN'}, {'days_estimate': '-Infinity'}, {'risk_score': '12'}, {'risk_score': True},
    {'days_estimate': 10 ** 400}, {'name': 7}, {'top_reasons': [7]}, {'warning': ['x']},
    {'last_maintenance': 5}, {'top_reasons': 'one reason'},
])
def test_a_field_of_the_wrong_type_is_refused_not_guessed_at(db_path, changes):
    """Text was being read as a number ("NaN" became NaN, which then broke the
    dashboard for everyone), and a number where text belongs caused a server error."""
    reply = _post(_dashboard(_component(**changes)))

    assert reply.status_code == 422
    assert _stored(db_path) is None


def test_a_time_of_the_wrong_type_is_refused(db_path):
    assert _post({'generated_at': 5, 'components': []}).status_code == 422


def test_what_is_stored_can_always_be_served(db_path):
    assert _post(json.dumps(_dashboard(_component(risk_score=float('nan'))))).status_code == 200

    json.dumps(_stored(db_path), allow_nan=False)                     # raises if NaN got through


@pytest.mark.parametrize('body', [b'{not json', b'', b'[1, 2', b'{"generated_at": NaN'])
def test_without_the_key_a_body_that_is_not_json_gets_the_same_answer_as_any_other(db_path, body):
    """The body used to be parsed before the key was checked: a different status,
    and work done for a caller who has not shown the key."""
    for key in (None, 'wrong-key'):
        assert _post(body, key=key).status_code == 404


def test_with_the_key_a_body_that_is_not_json_is_refused(db_path):
    assert _post(b'{not json').status_code == 422


def test_a_body_far_larger_than_any_dashboard_is_refused(db_path):
    reply = _post(json.dumps(_dashboard(_component(warning='W' * 600)) | {'padding': 'x' * 400_000}))

    assert reply.status_code == 413


@pytest.mark.parametrize('allowed, source, ok', [
    ('2001:db8::5', '2001:db8::5', True), ('2001:db8::5', '2001:db8::6', False),
    ('2001:db8::/64', '2001:db8::dead:beef', True), ('2001:db8::5/128', '2001:db8::5', True),
    ('203.0.113.7', '::ffff:203.0.113.7', True), ('203.0.113.7', '203.0.113.7:51234', True),
    ('203.0.113.7', '203.0.113.8', False),
])
def test_the_allowed_sources_are_matched_on_the_exact_address(db_path, monkeypatch, allowed, source, ok):
    monkeypatch.setenv('SYNC_ALLOWED_SOURCES', allowed)

    assert _post(_dashboard(), source=source).status_code == (200 if ok else 404)


def test_a_source_seen_before_is_not_recorded_again(db_path):
    """A site with two connections alternates between them; that is not news each time."""
    for source in (_FACILITY, _ELSEWHERE, _FACILITY, _ELSEWHERE, _FACILITY):
        assert _post(_dashboard(), source=source).status_code == 200

    assert [e[0] for e in _audit(db_path)] == ['sync_source_new', 'sync_source_new']


def test_a_flood_of_new_sources_cannot_fill_the_audit_log_or_slip_one_past_it(db_path, monkeypatch):
    """Every source that replaces the dashboard is named in the audit log: once the
    hour's allowance of new sources is spent, another new source is refused."""
    assert _post(_dashboard(), source=_FACILITY).status_code == 200
    statuses = [_post(_dashboard(_component(alert_level='GREEN')), source=f'198.51.100.{i}').status_code
                for i in range(60)]
    allowed = sync.MAX_SOURCE_ENTRIES_PER_HOUR - 1                     # the facility used one
    assert statuses == [200] * allowed + [404] * (60 - allowed)
    entries = _audit(db_path)
    assert len(entries) == sync.MAX_SOURCE_ENTRIES_PER_HOUR + 1
    assert 'refused until the hour is up' in entries[-1][2]
    named = ' '.join(e[2] for e in entries)
    assert all(f'198.51.100.{i}' in named for i in range(allowed))    # each one that got through is named

    assert _post(_dashboard(), source=_FACILITY).status_code == 200    # an established source is never held up

    started = sync._budgets['new']['started']
    monkeypatch.setattr(sync.time, 'monotonic', lambda: started + 3601)
    assert _post(_dashboard(), source='192.0.2.200').status_code == 200
    assert '192.0.2.200' in _audit(db_path)[-1][2]                     # an hour later a new source is taken again


def test_refused_sources_cannot_use_up_the_allowance_for_new_ones(db_path, monkeypatch):
    monkeypatch.setenv('SYNC_ALLOWED_SOURCES', f'{_FACILITY}, 198.51.100.0/24')
    for i in range(40):
        assert _post(_dashboard(), source=f'192.0.2.{i}').status_code == 404

    assert _post(_dashboard(), source='198.51.100.66').status_code == 200

    assert '198.51.100.66' in _audit(db_path)[-1][2]


@pytest.mark.parametrize('source, named', [
    ('::ffff:203.0.113.7', '203.0.113.7'), ('[::ffff:203.0.113.9]:4431', '203.0.113.9'),
    ('2001:db8:1:2:3:4:5:6', '2001:db8:1:2::/64'), ('203.0.113.7:51234', '203.0.113.7'),
])
def test_a_source_is_named_by_its_real_address(db_path, source, named):
    assert _post(_dashboard(), source=source).status_code == 200

    assert _audit(db_path)[-1][2] == f'first dashboard sync seen from {named}'


def test_the_address_in_regular_use_is_the_last_to_be_forgotten(db_path, monkeypatch):
    monkeypatch.setattr(sync, 'MAX_SOURCE_ENTRIES_PER_HOUR', 1000)
    assert _post(_dashboard(), source=_FACILITY).status_code == 200
    for i in range(sync.MAX_KNOWN_SOURCES + 4):
        assert _post(_dashboard(), source=f'198.51.100.{i}').status_code == 200
        assert _post(_dashboard(), source=_FACILITY).status_code == 200      # the facility keeps syncing

    assert [e[2] for e in _audit(db_path)].count(f'first dashboard sync seen from {_FACILITY}') == 1


def test_a_damaged_list_of_known_sources_does_not_stop_the_sync(db_path):
    assert _post(_dashboard()).status_code == 200
    conn = get_conn(db_path)
    conn.execute("UPDATE synced_dashboard SET sources=?", ['[' * 100000 + ']' * 100000])
    conn.commit()
    conn.close()

    assert _post(_dashboard()).status_code == 200


def test_a_flood_of_refused_sources_cannot_fill_the_audit_log_or_silence_it(db_path, monkeypatch):
    monkeypatch.setenv('SYNC_ALLOWED_SOURCES', _FACILITY)
    for i in range(60):
        assert _post(_dashboard(), source=f'198.51.100.{i}').status_code == 404
    assert len(_audit(db_path)) == sync.MAX_SOURCE_ENTRIES_PER_HOUR + 1

    started = sync._budgets['refused']['started']
    monkeypatch.setattr(sync.time, 'monotonic', lambda: started + 3601)
    assert _post(_dashboard(), source='192.0.2.200').status_code == 404
    assert '192.0.2.200' in _audit(db_path)[-1][2]


def test_a_burst_of_new_sources_cannot_push_out_the_established_one(db_path, monkeypatch):
    """Otherwise a key holder could make the facility look new, and then have it refused."""
    for _ in range(3):
        assert _post(_dashboard(), source=_FACILITY).status_code == 200
    for i in range(sync.MAX_SOURCE_ENTRIES_PER_HOUR + 10):
        _post(_dashboard(), source=f'198.51.100.{i}')

    assert _post(_dashboard(), source=_FACILITY).status_code == 200
    assert [e[2] for e in _audit(db_path)].count(f'first dashboard sync seen from {_FACILITY}') == 1


def test_a_list_of_known_sources_in_the_older_shape_is_still_read(db_path):
    assert _post(_dashboard()).status_code == 200
    conn = get_conn(db_path)
    conn.execute("UPDATE synced_dashboard SET sources=?", [json.dumps([_FACILITY])])
    conn.commit()
    conn.close()

    assert _post(_dashboard()).status_code == 200
    assert len(_audit(db_path)) == 1
