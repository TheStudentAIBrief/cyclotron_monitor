"""Per-user rate limit on the logged-in routes that cost model time or money.

/api/ask runs the local model, and the two photo routes run OCR (Gemini when
cloud OCR is switched on). They need a login, but without a limit one stolen or
misused account could keep the model busy or run up the OCR bill. The allowance
is per user, so one person hitting it does not lock out anyone else.
"""
import os
import tempfile

os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.gettempdir(), 'petlab_cost_limit_test.db'))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import config as _config  # noqa: E402
_config.get_config.cache_clear()

import api.main as main  # noqa: E402
from api.auth import get_current_user  # noqa: E402
from api.db_cloud import init_cloud_tables  # noqa: E402
from api.routes import ask, gauges  # noqa: E402

_LAB = 'petlabs-pretoria'


class _Reply:
    def raise_for_status(self):
        pass

    def json(self):
        return {'response': 'ok'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / 'petlab.db')
    monkeypatch.setenv('DATABASE_PATH', path)
    monkeypatch.setenv('LAB_ID', _LAB)
    _config.get_config.cache_clear()
    init_cloud_tables(path)
    monkeypatch.setattr(ask, 'ensure_running', lambda: None)
    monkeypatch.setattr(ask.httpx, 'post', lambda url, json=None, timeout=None: _Reply())
    monkeypatch.setattr(gauges, '_save_photo', lambda b64, db_path: None)
    monkeypatch.setattr(gauges, '_run_ocr', lambda b64, name: {'value': None})
    ask.reset_rate_limits()
    gauges.reset_rate_limits()
    previous = main.app.dependency_overrides.get(get_current_user)
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        ask.reset_rate_limits()
        gauges.reset_rate_limits()
        _config.get_config.cache_clear()
        main.app.dependency_overrides.pop(get_current_user, None)
        if previous is not None:
            main.app.dependency_overrides[get_current_user] = previous


def _as(username, role='admin'):
    main.app.dependency_overrides[get_current_user] = lambda: {'username': username, 'lab_id': _LAB, 'role': role}


def _ask(client):
    return client.post('/api/ask', json={'question': 'Is anything due?'}).status_code


def _photo(client):
    return client.post('/api/gauges/reading', json={'photo_b64': 'eHg=', 'gauge_name': '0096'}).status_code


def _eur(client):
    return client.post('/api/gauges/eur-photos', json={'photos_b64': [], 'filenames': []}).status_code


def test_asking_more_than_the_allowance_is_refused(client):
    _as('alice')
    statuses = [_ask(client) for _ in range(ask.MAX_ASKS_PER_MINUTE + 2)]
    assert statuses[:ask.MAX_ASKS_PER_MINUTE] == [200] * ask.MAX_ASKS_PER_MINUTE
    assert statuses[ask.MAX_ASKS_PER_MINUTE:] == [429, 429]


def test_one_user_running_out_does_not_lock_out_another(client):
    _as('alice')
    for _ in range(ask.MAX_ASKS_PER_MINUTE + 1):
        _ask(client)
    assert _ask(client) == 429
    _as('bob')
    assert _ask(client) == 200


def test_a_refusal_says_when_to_retry(client):
    _as('alice')
    for _ in range(ask.MAX_ASKS_PER_MINUTE):
        _ask(client)
    refused = client.post('/api/ask', json={'question': 'Is anything due?'})
    assert refused.status_code == 429
    assert refused.headers['retry-after'] == '60'


def test_photo_readings_over_the_allowance_are_refused(client):
    _as('olly', role='operator')
    statuses = [_photo(client) for _ in range(gauges.MAX_OCR_PER_MINUTE + 1)]
    assert statuses[:gauges.MAX_OCR_PER_MINUTE] == [200] * gauges.MAX_OCR_PER_MINUTE
    assert statuses[-1] == 429


def test_form_photo_imports_over_the_allowance_are_refused(client):
    _as('alice')
    statuses = [_eur(client) for _ in range(gauges.MAX_EUR_IMPORTS_PER_MINUTE + 1)]
    assert statuses[:gauges.MAX_EUR_IMPORTS_PER_MINUTE] == [200] * gauges.MAX_EUR_IMPORTS_PER_MINUTE
    assert statuses[-1] == 429
