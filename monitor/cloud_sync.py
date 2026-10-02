"""
Data bridge: POST the latest dashboard.json to the cloud API.

Called at the end of _refresh() in watcher.py. Any failure is logged at WARNING
level and never raises — local operation must never be interrupted by cloud issues.
"""
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

_log = logging.getLogger('cyclotron.cloud_sync')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends custom headers to wherever a redirect points, which would
    hand X-Sync-Key to another host (or to plain http). Never follow one -- the
    3xx then surfaces as an HTTPError and is logged like any other failure."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_open = urllib.request.build_opener(_NoRedirect).open


_CONFIG_PATH = Path(__file__).parent.parent / 'config.json'
_UNREADABLE = object()   # config.json exists but can't be parsed


def _load_config():
    """Parsed config.json; None if there is no file; _UNREADABLE if there is one
    but it can't be read (bad JSON, wrong encoding, e.g. saved with a BOM)."""
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (ValueError, OSError):   # JSONDecodeError and UnicodeDecodeError are ValueErrors
        return _UNREADABLE


def _key_in_config_file() -> bool:
    cfg = _load_config()
    return isinstance(cfg, dict) and bool(cfg.get('cloud_sync_key'))


def _read_cloud_cfg() -> tuple[str, str]:
    """Return (cloud_api_url, cloud_sync_key), or ('', '') if either is absent or
    not text. The address comes from config.json. The key comes from the
    CLOUD_SYNC_KEY environment variable if set, else from config.json -- the
    environment is preferred, so the key need not sit in a file in the app folder."""
    cfg = _load_config()
    if not isinstance(cfg, dict):
        return '', ''
    url = cfg.get('cloud_api_url', '')
    key = os.environ.get('CLOUD_SYNC_KEY') or cfg.get('cloud_sync_key', '')
    if not isinstance(url, str) or not isinstance(key, str):
        return '', ''
    return url, key


def _shown(url: str) -> str:
    """The address as it is safe to print or log: scheme and host only, because a
    key can end up inside the URL (user:KEY@host, ?key=KEY, or the two config
    values swapped)."""
    try:
        parts = urlsplit(url)
        return f'{parts.scheme}://{parts.hostname}' if parts.scheme and parts.hostname else '(not a web address)'
    except ValueError:
        return '(not a valid address)'


def _refusal(url: str) -> str | None:
    """Why this cloud_api_url must not be used, or None if it is fine."""
    # urllib.request.urlopen also supports file:// (and other) schemes; config.json
    # is admin-controlled, not user input, but this closes off that class of
    # SSRF/local-file-read defense-in-depth gap for near-zero cost.
    try:
        parts = urlsplit(url)
        parts.hostname   # noqa: B018 -- can itself raise on a malformed address
    except ValueError:
        return 'cloud_api_url is not a valid address'
    if parts.scheme not in ('http', 'https'):
        return 'cloud_api_url has an unsupported scheme'
    # The request carries the shared X-Sync-Key. Over plain http to another
    # machine it would cross the network in clear text, so only https is allowed
    # -- except to this machine itself, for local development.
    if parts.scheme == 'http' and parts.hostname not in ('localhost', '127.0.0.1'):
        return 'cloud_api_url must be https (the sync key would be sent unencrypted)'
    return None


def check_config() -> tuple[bool, str]:
    """Pre-flight for the facility PC: will the configured sync run?

        python -m monitor.cloud_sync

    Run it before and after updating the code there -- a refused address stops
    the sync, and the cloud dashboard then goes stale. Never prints the key
    (only the scheme and host of the address are shown)."""
    if _load_config() is _UNREADABLE:
        return False, ('REFUSED: config.json exists but cannot be read (not valid JSON, or not saved as '
                       'plain UTF-8) - nothing is being synced until this is fixed.')
    url, key = _read_cloud_cfg()
    if not url or not key:
        return True, 'Cloud sync is not configured (no cloud_api_url / cloud_sync_key) - nothing will be sent.'
    reason = _refusal(url)
    if reason:
        return False, (f'REFUSED: {reason}. Configured address: {_shown(url)} - '
                       'the sync will NOT run until this is fixed.')
    message = f'OK: the sync will run, to {_shown(url)}'
    if _key_in_config_file():
        message += (' Note: the sync key is stored in config.json. Set it as the CLOUD_SYNC_KEY '
                    'environment variable instead and remove it from the file.')
    return True, message


def sync_if_configured(dashboard_path: str) -> None:
    """Read dashboard.json and POST it to the cloud API if cloud config is present."""
    url, key = _read_cloud_cfg()
    if not url or not key:
        return
    reason = _refusal(url)
    if reason:
        # ERROR, not WARNING: nothing is being synced, so the cloud dashboard is going stale.
        _log.error('cloud_sync: %s, NOT syncing: %s', reason, _shown(url))
        return

    try:
        payload = Path(dashboard_path).read_bytes()
    except OSError as e:
        _log.warning('cloud_sync: cannot read %s: %s', dashboard_path, e)
        return

    endpoint = url.rstrip('/') + '/sync/dashboard'
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'X-Sync-Key': key,
        },
        method='POST',
    )
    try:
        with _open(req, timeout=10) as resp:
            _log.info('cloud_sync: synced dashboard → %s (HTTP %d)', _shown(url), resp.status)
    except urllib.error.HTTPError as e:
        _log.warning('cloud_sync: HTTP %d POSTing to %s', e.code, _shown(url))
    except Exception as e:
        _log.warning('cloud_sync: failed to reach %s: %s', _shown(url), e)


if __name__ == '__main__':
    import sys

    _ok, _message = check_config()
    # ascii-safe: an odd character in the address must not make the check itself crash
    print(_message.encode('ascii', 'backslashreplace').decode('ascii'))
    sys.exit(0 if _ok else 1)
