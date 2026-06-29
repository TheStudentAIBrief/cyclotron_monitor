from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()


class TokenRequest(BaseModel):
    token: str
    platform: Literal['ios', 'android'] = 'ios'


@router.post('/push/register')
def register_push_token(req: TokenRequest, user: dict = Depends(get_current_user)):
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn = get_conn(cfg['db_path'])
    try:
        conn.execute(
            "INSERT OR REPLACE INTO push_tokens (token, lab_id, platform, registered_at) "
            "VALUES (?,?,?,?)",
            [req.token, lab_id, req.platform, ts],
        )
        conn.commit()
        return {'status': 'registered'}
    finally:
        conn.close()
