import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.config import get_config

router = APIRouter()


@router.get('/dashboard')
def get_dashboard():
    cfg = get_config()
    p = Path(cfg['dashboard_path'])
    if not p.exists():
        raise HTTPException(503, detail='Dashboard not yet generated — run: python main.py predict')
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f'Dashboard read error: {e}')
