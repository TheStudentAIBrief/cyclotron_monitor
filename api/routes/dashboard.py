import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()


@router.get('/dashboard')
def get_dashboard(user: dict = Depends(get_current_user)):
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))

    # Primary: synced dashboard written by the on-prem data bridge
    db_path = cfg.get('db_path')
    if db_path:
        conn = get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT payload FROM synced_dashboard WHERE lab_id=?", [lab_id]
            ).fetchone()
            if row:
                return json.loads(row['payload'])
        finally:
            conn.close()

    # Fallback: local dashboard.json (works when API runs on-prem alongside the watcher)
    local_path = cfg.get('dashboard_path')
    if local_path:
        p = Path(local_path)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError) as e:
                raise HTTPException(500, detail=f'Dashboard read error: {e}')

    raise HTTPException(
        503,
        detail='No dashboard data available. Run "python main.py predict" then sync to cloud.'
    )
