import json
import os
from functools import lru_cache
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / 'config.json'


@lru_cache(maxsize=1)
def get_config() -> dict:
    """Load config once and cache for the lifetime of the process.

    Environment variables take precedence so the same code works both locally
    (config.json) and on the cloud VPS (env vars set by Render / Docker).
    """
    cfg: dict = {}
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Cloud deployment overrides
    if os.environ.get('DATABASE_PATH'):
        cfg['db_path'] = os.environ['DATABASE_PATH']
    if os.environ.get('LAB_ID'):
        cfg['lab_id'] = os.environ['LAB_ID']
    if os.environ.get('CLOUD_SYNC_KEY'):
        cfg['cloud_sync_key'] = os.environ['CLOUD_SYNC_KEY']

    return cfg
