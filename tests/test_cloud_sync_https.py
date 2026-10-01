"""The on-prem sync sends the shared X-Sync-Key and the dashboard to the cloud API.
It must never do that over plain HTTP to another machine -- a mistyped or
downgraded cloud_api_url would put the key on the wire in clear text.
"""
import pytest

from monitor import cloud_sync


@pytest.fixture
def sent(tmp_path, monkeypatch):
    """Capture what sync_if_configured() would send, without any network."""
    requests = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout=None):
        requests.append(req)
        return _Resp()

    monkeypatch.setattr(cloud_sync, '_open', _fake_urlopen)
    dashboard = tmp_path / 'dashboard.json'
    dashboard.write_text('{"ok": true}')

    def _sync(url):
        monkeypatch.setattr(cloud_sync, '_read_cloud_cfg', lambda: (url, 'the-sync-key'))
        cloud_sync.sync_if_configured(str(dashboard))
        return requests

    return _sync


@pytest.mark.parametrize('url', [
    'http://petlab-api-qad3.onrender.com',
    'http://192.168.4.46:8000',
    'HTTP://example.com',
])
def test_plain_http_to_another_machine_is_refused(sent, url):
    assert sent(url) == []


def test_https_is_sent_with_the_key(sent):
    requests = sent('https://petlab-api-qad3.onrender.com')
    assert len(requests) == 1
    assert requests[0].full_url == 'https://petlab-api-qad3.onrender.com/sync/dashboard'
    assert requests[0].get_header('X-sync-key') == 'the-sync-key'


@pytest.mark.parametrize('url', ['http://localhost:8000', 'http://127.0.0.1:8000'])
def test_plain_http_to_this_machine_is_still_allowed_for_local_development(sent, url):
    assert len(sent(url)) == 1


def test_refusal_is_logged_so_a_stalled_sync_can_be_diagnosed(sent, caplog):
    with caplog.at_level('WARNING', logger='cyclotron.cloud_sync'):
        sent('http://192.168.4.46:8000')
    assert 'https' in caplog.text.lower()


def test_a_redirect_is_not_followed_with_the_key_attached(tmp_path, monkeypatch):
    """urllib copies custom headers to wherever a redirect points -- so a redirect
    from the cloud endpoint would hand the sync key to another host."""
    import http.server
    import threading

    seen = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _serve(self):
            seen.append((self.path, self.headers.get('X-Sync-Key')))
            if self.path == '/sync/dashboard':
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/elsewhere')
            else:
                self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()

        do_GET = do_POST = _serve

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        dashboard = tmp_path / 'dashboard.json'
        dashboard.write_text('{}')
        monkeypatch.setattr(cloud_sync, '_read_cloud_cfg',
                            lambda: (f'http://127.0.0.1:{server.server_port}', 'the-sync-key'))
        cloud_sync.sync_if_configured(str(dashboard))
    finally:
        server.shutdown()
    assert [path for path, _ in seen] == ['/sync/dashboard']


# ── pre-flight check: run on the facility PC BEFORE and AFTER updating its code ──

@pytest.mark.parametrize('url,key,ok', [
    ('https://petlab-api-qad3.onrender.com', 'k', True),
    ('http://192.168.4.46:8000', 'k', False),      # would be refused -> sync would stop
    ('', '', True),                                 # sync not configured: nothing to break
])
def test_check_config_says_whether_the_sync_will_run(monkeypatch, url, key, ok):
    monkeypatch.setattr(cloud_sync, '_read_cloud_cfg', lambda: (url, key))
    result_ok, message = cloud_sync.check_config()
    assert result_ok is ok
    assert message and 'the-sync-key' not in message


def test_check_config_never_prints_the_key(monkeypatch):
    monkeypatch.setattr(cloud_sync, '_read_cloud_cfg', lambda: ('http://example.com', 'the-sync-key'))
    assert 'the-sync-key' not in cloud_sync.check_config()[1]


def test_a_refused_address_is_logged_as_an_error_not_a_warning(sent, caplog):
    """A stopped sync means stale dashboards at the facility -- it must stand out."""
    with caplog.at_level('WARNING', logger='cyclotron.cloud_sync'):
        sent('http://192.168.4.46:8000')
    assert [r.levelname for r in caplog.records] == ['ERROR']


@pytest.mark.parametrize('url', [
    'http://sync:the-sync-key@example.com',          # key embedded as a password
    'http://example.com/?key=the-sync-key',          # ...or as a query parameter
    'the-sync-key',                                  # the two config values swapped
])
def test_the_key_is_not_printed_or_logged_even_when_it_is_inside_the_address(monkeypatch, sent, caplog, url):
    monkeypatch.setattr(cloud_sync, '_read_cloud_cfg', lambda: (url, 'the-sync-key'))
    assert 'the-sync-key' not in cloud_sync.check_config()[1]
    with caplog.at_level('WARNING', logger='cyclotron.cloud_sync'):
        sent(url)
    assert 'the-sync-key' not in caplog.text


@pytest.mark.parametrize('content', ['[1, 2]', '"text"', 'null', '{"cloud_api_url": 5, "cloud_sync_key": "k"}',
                                     '{"cloud_api_url": "https://[::1", "cloud_sync_key": "k"}'])
def test_odd_config_gets_a_verdict_not_a_traceback(tmp_path, monkeypatch, content):
    cfg = tmp_path / 'config.json'
    cfg.write_text(content, encoding='utf-8')
    monkeypatch.setattr(cloud_sync, '_CONFIG_PATH', cfg)
    ok, message = cloud_sync.check_config()
    assert isinstance(ok, bool) and message
    dashboard = tmp_path / 'dashboard.json'
    dashboard.write_text('{}')
    cloud_sync.sync_if_configured(str(dashboard))     # documented as "never raises"


def test_a_config_file_that_cannot_be_read_is_not_reported_as_fine(tmp_path, monkeypatch):
    """e.g. saved with a byte-order mark, or cut short: nothing is being synced,
    which is exactly what the pre-flight exists to catch."""
    cfg = tmp_path / 'config.json'
    cfg.write_bytes(b'\xef\xbb\xbf{"cloud_api_url": "https://example.com", "cloud_sync_key": "k"')
    monkeypatch.setattr(cloud_sync, '_CONFIG_PATH', cfg)
    ok, message = cloud_sync.check_config()
    assert ok is False and 'config.json' in message
