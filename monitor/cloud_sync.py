"""
Data bridge: POST the latest dashboard.json to the cloud API.

Called at the end of _refresh() in watcher.py. Any failure is logged at WARNING
level and never raises — local operation must never be interrupted by cloud issues.
"""
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

_log = logging.getLogger('cyclotron.cloud_sync')


def _read_cloud_cfg() -> tuple[str, str]:
    """Return (cloud_api_url, cloud_sync_key) from config.json, or ('', '') on error."""
    config_path = Path(__file__).parent.parent / 'config.json'
    try:
        cfg = json.loads(config_path.read_text(encoding='utf-8'))
        return cfg.get('cloud_api_url', ''), cfg.get('cloud_sync_key', '')
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return '', ''


def sync_if_configured(dashboard_path: str) -> None:
    """Read dashboard.json and POST it to the cloud API if cloud config is present."""
    url, key = _read_cloud_cfg()
    if not url or not key:
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            _log.info('cloud_sync: synced dashboard → %s (HTTP %d)', endpoint, resp.status)
    except urllib.error.HTTPError as e:
        _log.warning('cloud_sync: HTTP %d POSTing to %s', e.code, endpoint)
    except Exception as e:
        _log.warning('cloud_sync: failed to reach %s: %s', endpoint, e)
