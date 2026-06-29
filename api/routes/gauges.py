import base64
import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()

_OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
_OLLAMA_MODEL = os.environ.get('GAUGE_OLLAMA_MODEL', 'qwen2.5vl:7b').strip()

_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "is_gauge": {"type": "boolean"},
        "reading_value": {"type": "number"},
        "unit": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "needle_reasoning": {"type": "string"},
    },
    "required": ["is_gauge", "reading_value", "unit", "confidence", "needle_reasoning"],
    "additionalProperties": False,
}

_OCR_PROMPT = (
    "Read the pressure gauge in this photo taken in a PET lab cyclotron facility. "
    "Identify the needle position against the printed scale. Report the numeric reading "
    "and units exactly as printed on the dial. If no gauge is present set is_gauge=false "
    "and reading_value=0."
)


class PhotoRequest(BaseModel):
    photo_b64: str
    gauge_name: str = ''


class ManualReadingRequest(BaseModel):
    gauge_name: str
    value: float
    unit: str = ''
    is_alert: bool = False
    alert_reason: str = ''


def _run_ocr(photo_b64: str) -> dict:
    """Extract a numeric reading from a gauge photo using a local Ollama vision model.

    Requires GAUGE_OLLAMA_MODEL env var (e.g. llava:7b) and Ollama running locally.
    Falls back to a stub result if not configured.
    """
    if not _OLLAMA_MODEL:
        return {
            'value': None, 'unit': '', 'is_alert': False, 'alert_reason': '',
            'raw_ocr_text': 'OCR not configured — set GAUGE_OLLAMA_MODEL env var (e.g. llava:7b)',
        }
    try:
        r = httpx.post(
            f'{_OLLAMA_HOST}/api/generate',
            json={
                'model': _OLLAMA_MODEL,
                'prompt': _OCR_PROMPT,
                'images': [photo_b64],
                'stream': False,
                'format': _OCR_SCHEMA,
                'options': {'temperature': 0},
            },
            timeout=120,
        )
        r.raise_for_status()
        result = json.loads(r.json().get('response', '{}'))
        value = result.get('reading_value') if result.get('is_gauge') else None
        reasoning = result.get('needle_reasoning', '')
        confidence = result.get('confidence', '?')
        return {
            'value': value,
            'unit': result.get('unit', ''),
            'is_alert': False,
            'alert_reason': '',
            'raw_ocr_text': f'{confidence} confidence — {reasoning}',
        }
    except Exception as e:
        return {
            'value': None, 'unit': '', 'is_alert': False, 'alert_reason': '',
            'raw_ocr_text': f'OCR error: {e.__class__.__name__}',
        }


def _save_photo(photo_b64: str, db_path: str) -> str:
    photos_dir = os.path.join(os.path.dirname(db_path), 'gauge_photos')
    os.makedirs(photos_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f'{ts}.jpg'
    try:
        with open(os.path.join(photos_dir, filename), 'wb') as f:
            f.write(base64.b64decode(photo_b64))
        return filename
    except Exception:
        return ''


@router.post('/gauges/reading')
def process_photo_reading(req: PhotoRequest, user: dict = Depends(get_current_user)):
    """Accept a gauge photo, attempt OCR, store the reading, return the result."""
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    photo_path = _save_photo(req.photo_b64, cfg['db_path'])
    result = _run_ocr(req.photo_b64)
    conn = get_conn(cfg['db_path'])
    try:
        cur = conn.execute(
            "INSERT INTO gauge_readings "
            "(lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason, photo_path, raw_ocr_text) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [lab_id, req.gauge_name, ts, result['value'], result['unit'],
             int(result['is_alert']), result['alert_reason'], photo_path, result['raw_ocr_text']],
        )
        conn.commit()
        return {'id': cur.lastrowid, 'timestamp': ts, 'gauge_name': req.gauge_name, **result}
    finally:
        conn.close()


@router.post('/gauges')
def submit_manual_reading(req: ManualReadingRequest, user: dict = Depends(get_current_user)):
    """Submit a gauge reading entered manually (no photo required)."""
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn = get_conn(cfg['db_path'])
    try:
        cur = conn.execute(
            "INSERT INTO gauge_readings (lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason) "
            "VALUES (?,?,?,?,?,?,?)",
            [lab_id, req.gauge_name, ts, req.value, req.unit, int(req.is_alert), req.alert_reason],
        )
        conn.commit()
        return {'id': cur.lastrowid, 'timestamp': ts, **req.model_dump()}
    finally:
        conn.close()


@router.get('/gauges')
def list_gauges(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    gauge_name: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    offset = (page - 1) * per_page
    conn = get_conn(cfg['db_path'])
    try:
        if gauge_name:
            rows = conn.execute(
                "SELECT * FROM gauge_readings WHERE lab_id=? AND gauge_name LIKE ? "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [lab_id, f'%{gauge_name}%', per_page, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM gauge_readings WHERE lab_id=? "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [lab_id, per_page, offset],
            ).fetchall()
        return {'page': page, 'per_page': per_page, 'items': [dict(r) for r in rows]}
    finally:
        conn.close()
