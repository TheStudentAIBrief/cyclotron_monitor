"""The facility dashboard server must never be reachable from the lab network
over plain HTTP or without a login. On this machine only (127.0.0.1) it may run
without either; on any other address it needs both TLS and credentials."""
import json

import pytest

import serve


class _Started(Exception):
    """Raised by the stand-in server: start_server got as far as binding."""


@pytest.fixture
def ready(tmp_path, monkeypatch):
    """A dashboard, a UI folder and a stand-in for the real server (nothing is bound)."""
    bound = {}

    class _FakeServer:
        def __init__(self, address, handler):
            bound['address'] = address
            raise _Started

    monkeypatch.setattr(serve, '_BoundedHTTPServer', _FakeServer)
    (tmp_path / 'ui').mkdir()
    (tmp_path / 'dashboard.json').write_text('{}', encoding='utf-8')
    creds = tmp_path / '.credentials.json'
    creds.write_text(json.dumps({'username': 'operator', 'hash': serve.hash_password('a-long-enough-password')}),
                     encoding='utf-8')
    return {'tmp': tmp_path, 'bound': bound, 'creds': str(creds)}


def _start(ready, host, credentials=False, tls_dir=None):
    serve.start_server(str(ready['tmp'] / 'dashboard.json'), ready['tmp'] / 'ui', host=host,
                       credentials_path=ready['creds'] if credentials else None, tls_dir=tls_dir)


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', '127.0.0.2'])
def test_on_this_machine_only_it_starts_without_tls(ready, host):
    with pytest.raises(_Started):
        _start(ready, host)

    assert ready['bound']['address'][0] == host


@pytest.mark.parametrize('host', ['0.0.0.0', '192.168.1.20', '::', 'facility-pc', '', ' ', '0', 'LOCALHOST.'])
def test_on_the_network_it_refuses_to_start_without_tls(ready, host):
    with pytest.raises(SystemExit, match='TLS'):
        _start(ready, host, credentials=True)

    assert ready['bound'] == {}


def test_on_the_network_it_refuses_to_start_without_a_login(ready, monkeypatch):
    monkeypatch.setattr(serve, '_load_tls', lambda tls_dir: object())     # TLS is in place

    with pytest.raises(SystemExit, match='login'):
        _start(ready, '0.0.0.0', credentials=False, tls_dir=str(ready['tmp']))

    assert ready['bound'] == {}


def test_on_the_network_it_starts_with_tls_and_a_login(ready, monkeypatch):
    monkeypatch.setattr(serve, '_load_tls', lambda tls_dir: object())

    with pytest.raises(_Started):
        _start(ready, '0.0.0.0', credentials=True, tls_dir=str(ready['tmp']))

    assert ready['bound']['address'] == ('0.0.0.0', 8443)
