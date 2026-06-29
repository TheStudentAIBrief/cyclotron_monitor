import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / 'config.json'


def get_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return json.load(f)
