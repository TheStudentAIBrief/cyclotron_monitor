"""Bulk-import endpoints for pushing local data to a cloud deploy with no
shell/file access (e.g. Render free tier). Reuses the standard JWT auth
already applied to every /api/* router in main.py - no new auth surface.

One-off/periodic use, not part of the live ingestion path: local ingest.py
still writes to the local DB directly; these endpoints are how that data
later reaches the cloud DB, via scripts/push_data_to_cloud.py.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.config import get_config
from api.db_cloud import get_conn

router = APIRouter()

# Bounds a single request's DB work and JSON body size — mirrors the pattern
# already used for EUR photo batches (_MAX_EUR_PHOTOS) and CSV imports
# (_MAX_CSV_BYTES) in api/routes/gauges.py.
_MAX_ROWS_PER_REQUEST = 2000


class BeamDailyRow(BaseModel):
    date: str
    param: str
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    p10: float | None = None
    p90: float | None = None
    data_quality: str = 'ok'


class BeamDailyImport(BaseModel):
    rows: list[BeamDailyRow]


class PetraceBatchRow(BaseModel):
    batch_no: int
    batch_date: str
    tracer_num: int = 0
    tracer_name: str = ''
    site: str = ''
    duration_s: float | None = 0
    row_count: int = 0
    foil_no: int | None = None
    peak_target_uA: float | None = 0
    avg_target_uA: float | None = 0
    total_muAh: float | None = 0
    avg_arc_I: float | None = 0
    avg_vacuum_P: float | None = 0
    peak_vacuum_P: float | None = 0
    rf_efficiency: float | None = 0
    ingested_at: str


class PetraceBatchImport(BaseModel):
    rows: list[PetraceBatchRow]


class MaintenanceEventRow(BaseModel):
    timestamp: str
    component_key: str
    component_label: str
    source_file: str | None = None


class MaintenanceEventImport(BaseModel):
    rows: list[MaintenanceEventRow]


class PredictionRow(BaseModel):
    run_at: str
    component: str
    risk_score: float | None = None
    days_estimate: float | None = None
    alert_level: str = ''
    primary_signal: str = ''
    top_features: str | None = None


class PredictionImport(BaseModel):
    rows: list[PredictionRow]


class EventRow(BaseModel):
    timestamp: str
    severity: str | None = ''
    code: str | None = ''
    function: str | None = ''
    message: str | None = ''
    source_file: str | None = None


class EventImport(BaseModel):
    rows: list[EventRow]


class GaugeReadingRow(BaseModel):
    lab_id: str
    gauge_name: str = ''
    timestamp: str
    value: float | None = None
    unit: str = ''
    is_alert: int = 0
    alert_reason: str = ''
    photo_path: str = ''
    raw_ocr_text: str = ''
    location: str = ''
    alert_lo: float | None = None
    alert_hi: float | None = None
    action_lo: float | None = None
    action_hi: float | None = None
    confidence: str = ''
    verified_by: str = ''
    verified_at: str = ''


class GaugeReadingImport(BaseModel):
    rows: list[GaugeReadingRow]


def _check_batch_size(rows: list) -> None:
    if len(rows) > _MAX_ROWS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f'Batch of {len(rows)} rows exceeds the {_MAX_ROWS_PER_REQUEST}-row limit per request.',
        )


@router.post('/admin/import/beam-daily')
def import_beam_daily(payload: BeamDailyImport):
    _check_batch_size(payload.rows)
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        for row in payload.rows:
            conn.execute(
                "INSERT OR REPLACE INTO beam_daily VALUES (?,?,?,?,?,?,?,?,?)",
                [row.date, row.param, row.mean, row.std, row.min, row.max,
                 row.p10, row.p90, row.data_quality],
            )
        conn.commit()
        return {'inserted': len(payload.rows)}
    finally:
        conn.close()


@router.post('/admin/import/petrace-batches')
def import_petrace_batches(payload: PetraceBatchImport):
    _check_batch_size(payload.rows)
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        for row in payload.rows:
            conn.execute(
                "INSERT OR REPLACE INTO petrace_batches VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [row.batch_no, row.batch_date, row.tracer_num, row.tracer_name,
                 row.site, row.duration_s, row.row_count, row.foil_no,
                 row.peak_target_uA, row.avg_target_uA, row.total_muAh,
                 row.avg_arc_I, row.avg_vacuum_P, row.peak_vacuum_P,
                 row.rf_efficiency, row.ingested_at],
            )
        conn.commit()
        return {'inserted': len(payload.rows)}
    finally:
        conn.close()


@router.post('/admin/import/maintenance-events')
def import_maintenance_events(payload: MaintenanceEventImport):
    _check_batch_size(payload.rows)
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        for row in payload.rows:
            conn.execute(
                "INSERT OR REPLACE INTO maintenance_events VALUES (?,?,?,?)",
                [row.timestamp, row.component_key, row.component_label, row.source_file],
            )
        conn.commit()
        return {'inserted': len(payload.rows)}
    finally:
        conn.close()


@router.post('/admin/import/predictions')
def import_predictions(payload: PredictionImport):
    _check_batch_size(payload.rows)
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        for row in payload.rows:
            conn.execute(
                "INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?)",
                [row.run_at, row.component, row.risk_score, row.days_estimate,
                 row.alert_level, row.primary_signal, row.top_features],
            )
        conn.commit()
        return {'inserted': len(payload.rows)}
    finally:
        conn.close()


@router.post('/admin/import/events')
def import_events(payload: EventImport):
    _check_batch_size(payload.rows)
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        for row in payload.rows:
            conn.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)",
                [row.timestamp, row.severity, row.code, row.function, row.message, row.source_file],
            )
        conn.commit()
        return {'inserted': len(payload.rows)}
    finally:
        conn.close()


@router.post('/admin/import/gauge-readings')
def import_gauge_readings(payload: GaugeReadingImport):
    _check_batch_size(payload.rows)
    cfg = get_config()
    conn = get_conn(cfg['db_path'])
    try:
        inserted = 0
        for row in payload.rows:
            existing = conn.execute(
                "SELECT 1 FROM gauge_readings WHERE lab_id=? AND gauge_name=? AND timestamp=?",
                [row.lab_id, row.gauge_name, row.timestamp],
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO gauge_readings "
                "(lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason, "
                " photo_path, raw_ocr_text, location, alert_lo, alert_hi, action_lo, "
                " action_hi, confidence, verified_by, verified_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [row.lab_id, row.gauge_name, row.timestamp, row.value, row.unit,
                 row.is_alert, row.alert_reason, row.photo_path, row.raw_ocr_text,
                 row.location, row.alert_lo, row.alert_hi, row.action_lo,
                 row.action_hi, row.confidence, row.verified_by, row.verified_at],
            )
            inserted += 1
        conn.commit()
        return {'inserted': inserted}
    finally:
        conn.close()
