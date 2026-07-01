"""
Retry/backoff behavior for api.gemini_ocr.call() (transient 429/5xx failures).

No DB dependency here — gemini_ocr.py never touches the DB, so these tests
import it directly without any DATABASE_PATH setup.
"""
import os

os.environ.setdefault('GEMINI_API_KEY', 'test-key')

import httpx
import pytest

from api import gemini_ocr


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request('POST', 'https://example.com')
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f'{self.status_code} error', request=request, response=response,
            )

    def json(self):
        return self._json_data


def _ok_json():
    return {'candidates': [{'content': {'parts': [{'text': '{"ok": true}'}]}}]}


@pytest.fixture(autouse=True)
def _ensure_gemini_key(monkeypatch):
    # gemini_ocr reads GEMINI_API_KEY at import time; in the full suite the module
    # can load (via api.main) before this file's os.environ.setdefault runs, leaving
    # the module global empty. Set it directly so these retry tests are order-independent.
    monkeypatch.setattr(gemini_ocr, 'GEMINI_API_KEY', 'test-key')


def test_call_retries_on_429_then_succeeds(monkeypatch):
    calls = []
    sleeps = []
    responses = [
        FakeResponse(429),
        FakeResponse(429),
        FakeResponse(200, _ok_json()),
    ]

    def fake_post(*args, **kwargs):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(gemini_ocr, '_sleep', lambda s: sleeps.append(s), raising=False)

    result = gemini_ocr.call('prompt', 'aW1n', {'type': 'object'})

    assert result == '{"ok": true}'
    assert len(calls) == 3


def test_call_respects_retry_after_header(monkeypatch):
    sleeps = []
    responses = [
        FakeResponse(429, headers={'Retry-After': '3'}),
        FakeResponse(200, _ok_json()),
    ]
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(gemini_ocr, '_sleep', lambda s: sleeps.append(s), raising=False)

    gemini_ocr.call('prompt', 'aW1n', {'type': 'object'})

    assert len(sleeps) == 1
    assert sleeps[0] == 3


def test_call_uses_exponential_backoff_without_retry_after_header(monkeypatch):
    sleeps = []
    responses = [
        FakeResponse(429),
        FakeResponse(429),
        FakeResponse(429),
        FakeResponse(200, _ok_json()),
    ]
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(gemini_ocr, '_sleep', lambda s: sleeps.append(s), raising=False)

    gemini_ocr.call('prompt', 'aW1n', {'type': 'object'})

    assert len(sleeps) == 3
    assert 2 <= sleeps[0] <= 2.5
    assert 4 <= sleeps[1] <= 4.5
    assert 8 <= sleeps[2] <= 8.5


def test_call_gives_up_after_max_retries_and_raises(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(429)

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(gemini_ocr, '_sleep', lambda s: None, raising=False)

    with pytest.raises(httpx.HTTPStatusError):
        gemini_ocr.call('prompt', 'aW1n', {'type': 'object'})

    assert len(calls) == 4


def test_call_does_not_retry_non_transient_4xx(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(400)

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(gemini_ocr, '_sleep', lambda s: None, raising=False)

    with pytest.raises(httpx.HTTPStatusError):
        gemini_ocr.call('prompt', 'aW1n', {'type': 'object'})

    assert len(calls) == 1


def test_call_retries_on_5xx_transient_error(monkeypatch):
    calls = []
    responses = [
        FakeResponse(500),
        FakeResponse(200, _ok_json()),
    ]

    def fake_post(*args, **kwargs):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(httpx, 'post', fake_post)
    monkeypatch.setattr(gemini_ocr, '_sleep', lambda s: None, raising=False)

    result = gemini_ocr.call('prompt', 'aW1n', {'type': 'object'})

    assert result == '{"ok": true}'
    assert len(calls) == 2
