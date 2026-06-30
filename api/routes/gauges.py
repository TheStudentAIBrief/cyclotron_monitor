import base64
import csv
import io
import json
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from api.auth import get_current_user
from api.config import get_config
from api.db_cloud import get_conn
from monitor.eur_form_parser import EUR_OCR_PROMPT, EUR_OCR_SCHEMA, parse_eur_response
from monitor.gauge_archive import archive_import

router = APIRouter()

_OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
_OLLAMA_MODEL = os.environ.get('GAUGE_OLLAMA_MODEL', 'qwen2.5vl:7b').strip()

# Bounds so a single authenticated request can't exhaust CPU/memory/disk. Each EUR
# photo triggers a multi-second vision-model call, so cap the batch; cap the CSV so it
# cannot be read unbounded into memory.
_MAX_EUR_PHOTOS = int(os.environ.get('MAX_EUR_PHOTOS', '20'))
_MAX_CSV_BYTES = int(os.environ.get('MAX_CSV_BYTES', str(10 * 1024 * 1024)))

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


def _gauge_status(value, alert_lo, alert_hi, action_lo, action_hi) -> str:
    """Mirrors cofounder's gauge_tool._classify() exactly: ACTION > ALERT > NORMAL."""
    if value is None:
        return 'UNKNOWN'
    if action_lo is not None and value < action_lo:
        return 'ACTION'
    if action_hi is not None and value > action_hi:
        return 'ACTION'
    if alert_lo is not None and value < alert_lo:
        return 'ALERT'
    if alert_hi is not None and value > alert_hi:
        return 'ALERT'
    return 'NORMAL'


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
    if os.environ.get("OLLAMA_NEWSLETTER_ONLY") == "1":
        return {
            'value': None, 'unit': '', 'is_alert': False, 'alert_reason': '',
            'raw_ocr_text': 'Ollama restricted to newsletter tasks (OLLAMA_NEWSLETTER_ONLY=1)',
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
    filename = f'{ts}_{uuid.uuid4().hex[:8]}.jpg'   # uuid suffix avoids same-second collisions
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
    # Reject inf/nan with a clean error — stored, they would corrupt the gauge-listing JSON.
    if not math.isfinite(req.value):
        raise HTTPException(status_code=422, detail='value must be a finite number')
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
        items = []
        for r in rows:
            d = dict(r)
            status = _gauge_status(d.get('value'), d.get('alert_lo'), d.get('alert_hi'),
                                   d.get('action_lo'), d.get('action_hi'))
            # is_alert=1 set manually (no thresholds) must not be silently downgraded to NORMAL
            if status == 'NORMAL' and d.get('is_alert'):
                status = 'ALERT'
            d['status'] = status
            items.append(d)
        return {'page': page, 'per_page': per_page, 'items': items}
    finally:
        conn.close()


@router.post('/gauges/import-csv')
async def import_gauge_csv(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Import gauge readings from the cofounder's gauge_readings.csv format.

    Expected CSV columns: gauge, location, date, value_Pa,
    alert_lo, alert_hi, action_lo, action_hi, confidence,
    created_by, verified_by, verified_at
    """
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    content = await file.read(_MAX_CSV_BYTES + 1)
    if len(content) > _MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f'CSV exceeds the {_MAX_CSV_BYTES}-byte limit.')
    text = content.decode('utf-8-sig')  # handle BOM from Excel exports
    reader = csv.DictReader(io.StringIO(text))

    def _float(val):
        try:
            f = float(val) if val not in (None, '', 'None', 'nan') else None
        except (ValueError, TypeError):
            return None
        return f if (f is None or math.isfinite(f)) else None   # drop inf/nan

    inserted, errors = 0, []
    conn = get_conn(cfg['db_path'])
    try:
        for i, row in enumerate(reader):
            try:
                gauge    = str(row.get('gauge', '')).strip()
                location = str(row.get('location', '')).strip()
                date     = str(row.get('date', '')).strip()[:10]
                value_pa = _float(row.get('value_Pa'))
                alert_lo  = _float(row.get('alert_lo'))
                alert_hi  = _float(row.get('alert_hi'))
                action_lo = _float(row.get('action_lo'))
                action_hi = _float(row.get('action_hi'))
                confidence   = str(row.get('confidence', 'import')).strip()
                verified_by  = str(row.get('verified_by', '')).strip()
                verified_at  = str(row.get('verified_at', '')).strip()

                status   = _gauge_status(value_pa, alert_lo, alert_hi, action_lo, action_hi)
                is_alert = 1 if status in ('ALERT', 'ACTION') else 0
                ts       = f'{date}T00:00:00Z' if date else datetime.now(timezone.utc).isoformat(timespec='seconds')

                conn.execute(
                    "INSERT INTO gauge_readings "
                    "(lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason, "
                    "location, alert_lo, alert_hi, action_lo, action_hi, confidence, verified_by, verified_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [lab_id, gauge, ts, value_pa, 'Pa', is_alert, status,
                     location, alert_lo, alert_hi, action_lo, action_hi,
                     confidence, verified_by, verified_at],
                )
                inserted += 1
            except Exception as exc:
                errors.append(f'row {i + 2}: {exc.__class__.__name__}: {exc}')
        conn.commit()
    finally:
        conn.close()
    return {'inserted': inserted, 'errors': errors}


@router.delete('/gauges/{reading_id}')
def delete_gauge_reading(reading_id: int, user: dict = Depends(get_current_user)):
    """Permanently delete a single gauge reading by ID.

    NNR audit: the deletion (actor + prior content) is recorded in audit_log *before*
    the row is removed, so a regulated record can never disappear without a trace.
    """
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    conn = get_conn(cfg['db_path'])
    try:
        row = conn.execute(
            "SELECT * FROM gauge_readings WHERE id=? AND lab_id=?", [reading_id, lab_id]
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail='Reading not found')
        ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
        conn.execute(
            "INSERT INTO audit_log (ts, action, lab_id, actor, detail) VALUES (?,?,?,?,?)",
            [ts, 'delete_gauge_reading', lab_id, user.get('username', ''), json.dumps(dict(row))],
        )
        conn.execute("DELETE FROM gauge_readings WHERE id=? AND lab_id=?", [reading_id, lab_id])
        conn.commit()
        return {'deleted': reading_id}
    finally:
        conn.close()


class EurPhotosRequest(BaseModel):
    photos_b64: list[str]
    filenames: list[str] = []


@router.post('/gauges/eur-photos')
def import_eur_photos(req: EurPhotosRequest, user: dict = Depends(get_current_user)):
    """Accept one or more EUR form photos (base64), run OCR, archive, and bulk-insert readings.

    Returns total readings inserted and any per-photo errors.
    """
    if len(req.photos_b64) > _MAX_EUR_PHOTOS:
        raise HTTPException(
            status_code=413,
            detail=f'Too many photos in one request (max {_MAX_EUR_PHOTOS}).',
        )
    cfg = get_config()
    lab_id = user.get('lab_id', cfg.get('lab_id', 'default'))
    archive_dir = os.path.join(os.path.dirname(cfg['db_path']), 'gauge_archive')
    inserted, errors = 0, []

    for i, b64 in enumerate(req.photos_b64):
        filename = req.filenames[i] if i < len(req.filenames) else f'upload_{i}.jpg'
        try:
            photo_bytes = base64.b64decode(b64)
            r = httpx.post(
                f'{_OLLAMA_HOST}/api/generate',
                json={
                    'model': _OLLAMA_MODEL,
                    'prompt': EUR_OCR_PROMPT,
                    'images': [b64],
                    'stream': False,
                    'format': EUR_OCR_SCHEMA,
                    'options': {'temperature': 0, 'num_ctx': 4096},
                },
                timeout=600,
            )
            r.raise_for_status()
            ocr_raw = r.json().get('response', '{}')
            readings = parse_eur_response(ocr_raw)
            archive_import(filename, photo_bytes, ocr_raw, readings, archive_dir)

            conn = get_conn(cfg['db_path'])
            try:
                for row in readings:
                    conn.execute(
                        "INSERT INTO gauge_readings "
                        "(lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason, "
                        "location, alert_lo, alert_hi, action_lo, action_hi, confidence, verified_by) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            lab_id, row['gauge_name'], row['timestamp'], row['value'],
                            row['unit'], row['is_alert'], row['alert_reason'],
                            row.get('location', ''), row.get('alert_lo'), row.get('alert_hi'),
                            row.get('action_lo'), row.get('action_hi'),
                            row.get('confidence', 'eur_form'), row.get('verified_by', ''),
                        ],
                    )
                    inserted += 1
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            errors.append({'file': filename, 'error': f'{exc.__class__.__name__}: {exc}'})

    return {'inserted': inserted, 'errors': errors}
