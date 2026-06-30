"""
Equipment Usage Record (EUR) form parser for PET Labs cyclotron operational logs.

Each EUR form is a photographed paper sheet with 2-3 operational run entries.
Each entry records: Date (DD/MM/YYYY), Operator, Gas Flow (Sccm), Vacuum (Torr),
IS Current (A), Beam on Post (µA), and cabinet check statuses.

parse_eur_response() converts Ollama's structured JSON into gauge_readings row dicts,
one row per measurement type per entry.
"""
import json
from datetime import datetime

# Gauge definitions: (name, unit, json_field, alert_lo, alert_hi, action_lo, action_hi)
# For vacuum: higher pressure = worse vacuum, so only _hi thresholds apply.
# All thresholds are operational limits observed from PET Labs Pretoria EUR forms.
EUR_GAUGES = [
    ('Gas Flow',     'Sccm', 'gas_flow_sccm',   5.5,  7.0,  5.0,  8.0),
    ('Vacuum',       'Torr', 'vacuum_torr',      None, 5e-7, None, 1e-6),
    ('IS Current',   'A',    'is_current_a',     0.10, 0.25, 0.08, 0.30),
    ('Beam on Post', 'µA',   'beam_on_post_ua',  50.0, None, 30.0, None),
]

EUR_OCR_PROMPT = (
    "You are analysing a PET Labs Equipment Usage Record (EUR) form for an IBA Cyclone 18/9 "
    "cyclotron. The form is a photographed paper sheet with multiple operational run sections "
    "arranged top-to-bottom, typically 2-3 per page. Each section contains: "
    "date (DD/MM/YYYY format, e.g. '04/02/2023'), operator initials or name, "
    "gas flow in Sccm (typically 5.5-7.5), vacuum in Torr shown in scientific notation "
    "(e.g. '1.6 × 10⁻⁷' — return as 1.6e-7), IS current in Amperes (0.08-0.30 A), "
    "and beam on post in µA (typically 30-350 µA). "
    "Extract ALL visible entries from top to bottom. "
    "If the form is not a EUR/equipment usage record, return entries: []."
)

EUR_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date":             {"type": "string"},
                    "operator":         {"type": "string"},
                    "gas_flow_sccm":    {"type": ["number", "null"]},
                    "vacuum_torr":      {"type": ["number", "null"]},
                    "is_current_a":     {"type": ["number", "null"]},
                    "beam_on_post_ua":  {"type": ["number", "null"]},
                    "rear_cabinet_ok":  {"type": ["boolean", "null"]},
                    "feed_cabinet_ok":  {"type": ["boolean", "null"]},
                    "comments":         {"type": "string"},
                },
                "required": ["date"],
            },
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}


def _gauge_status(value, alert_lo, alert_hi, action_lo, action_hi) -> str:
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


def _parse_date(date_str: str) -> str:
    """Convert DD/MM/YYYY or D/M/YYYY to YYYY-MM-DD. Returns the input unchanged if unparseable."""
    s = (date_str or '').strip()
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%-d/%-m/%Y'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    return s


def parse_eur_response(ocr_json: str) -> list[dict]:
    """Parse Ollama's EUR form JSON response into gauge_readings-compatible row dicts.

    Returns an empty list for any bad input — never raises.
    Each entry in the JSON produces one row per gauge type that has a non-None value.
    """
    try:
        data = json.loads(ocr_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    entries = data.get('entries')
    if not isinstance(entries, list):
        return []

    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        date_iso = _parse_date(entry.get('date', ''))
        ts = f'{date_iso}T00:00:00Z' if date_iso else ''
        operator = str(entry.get('operator') or '')

        for name, unit, field, alert_lo, alert_hi, action_lo, action_hi in EUR_GAUGES:
            raw = entry.get(field)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue

            status = _gauge_status(value, alert_lo, alert_hi, action_lo, action_hi)
            rows.append({
                'gauge_name':  name,
                'timestamp':   ts,
                'value':       value,
                'unit':        unit,
                'is_alert':    1 if status in ('ALERT', 'ACTION') else 0,
                'alert_reason': status if status != 'NORMAL' else '',
                'alert_lo':    alert_lo,
                'alert_hi':    alert_hi,
                'action_lo':   action_lo,
                'action_hi':   action_hi,
                'confidence':  'eur_form',
                'location':    'Control Room',
                'verified_by': operator,
            })

    return rows
