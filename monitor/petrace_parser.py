"""PETrace 800 log parser.

Each log file covers one production batch. Format:
  Line 0: "Tracer: (N) Name\\t\\t\\tBatch no: N\\t\\t\\tDate: YYYY-MM-DD"
  Line 1: "Site name: <name>"
  Line 2: blank
  Line 3: tab-separated column headers (26 columns)
  Lines 4+: tab-separated data rows (~3-second intervals)
"""
import re
from datetime import datetime
from typing import Optional

COLUMNS = [
    'time', 'arc_I', 'arc_V', 'gas_flow',
    'dee1_kV', 'dee2_kV', 'magnet_I',
    'foil_I', 'coll_l_I', 'target_I', 'coll_r_I',
    'vacuum_P', 'target_P', 'delta_dee_kV', 'phase_load',
    'dee_ref_V', 'probe_I', 'he_cool_P',
    'flap1_pos', 'flap2_pos', 'step_pos', 'extr_pos',
    'balance', 'rf_fwd_W', 'rf_refl_W', 'foil_no',
]

_HEADER_RE = re.compile(
    r'Tracer:\s*\((\d+)\)\s*(.*?)\s*Batch no:\s*(\d+)\s*Date:\s*(.+)',
    re.IGNORECASE,
)
_SITE_RE = re.compile(r'Site name:\s*(.+)', re.IGNORECASE)


def _normalise_date(raw: str) -> str:
    """'2025-02- 5' → '2025-02-05'."""
    parts = [p.strip().zfill(2) for p in raw.strip().split('-')]
    return '-'.join(parts)


def _parse_time(t: str) -> Optional[int]:
    """HH:MM:SS → total seconds since midnight. Returns None on parse failure."""
    t = t.strip()
    try:
        h, m, s = t.split(':')
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return None


def parse_header(text: str) -> dict:
    """Extract batch metadata from the first lines of a log file."""
    result = {'batch_no': 0, 'batch_date': '', 'tracer_num': 0, 'tracer_name': '', 'site': ''}
    for line in text.splitlines():
        m = _HEADER_RE.search(line)
        if m:
            result['tracer_num'] = int(m.group(1))
            result['tracer_name'] = m.group(2).strip()
            result['batch_no'] = int(m.group(3))
            result['batch_date'] = _normalise_date(m.group(4))
        sm = _SITE_RE.search(line)
        if sm:
            result['site'] = sm.group(1).strip()
    return result


def parse_rows(text: str) -> list[dict]:
    """Parse all data rows from a log file. Returns list of dicts keyed by COLUMNS."""
    rows = []
    in_data = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('Time\t') or stripped.startswith('Time '):
            in_data = True
            continue
        if not in_data or not stripped:
            continue
        parts = line.split('\t')
        if len(parts) < len(COLUMNS):
            continue
        # First part is "HH:MM:SS " (trailing space) — time column
        row: dict = {}
        try:
            row['time'] = parts[0].strip()
            for i, col in enumerate(COLUMNS[1:], start=1):
                val = parts[i].strip()
                if col == 'foil_no':
                    row[col] = int(float(val))
                else:
                    row[col] = float(val)
        except (ValueError, IndexError):
            continue
        rows.append(row)
    return rows


def summarise(rows: list[dict]) -> dict:
    """Compute batch-level statistics from parsed data rows."""
    if not rows:
        return {
            'peak_target_uA': 0.0,
            'avg_target_uA': 0.0,
            'total_muAh': 0.0,
            'duration_s': 0.0,
            'foil_no': None,
            'avg_arc_I': 0.0,
            'avg_vacuum_P': 0.0,
            'peak_vacuum_P': 0.0,
            'rf_efficiency': 0.0,
        }

    targets = [r['target_I'] for r in rows]
    arc_Is = [r['arc_I'] for r in rows]
    vacuums = [r['vacuum_P'] for r in rows]

    # µAh: trapezoidal integration over consecutive rows
    total_muAh = 0.0
    t_prev = _parse_time(rows[0]['time'])
    for i in range(1, len(rows)):
        t_curr = _parse_time(rows[i]['time'])
        if t_prev is not None and t_curr is not None:
            dt_h = (t_curr - t_prev) / 3600.0
            if dt_h > 0:
                avg_I = (rows[i - 1]['target_I'] + rows[i]['target_I']) / 2.0
                total_muAh += avg_I * dt_h
        t_prev = t_curr

    # Duration: last_time - first_time
    t0 = _parse_time(rows[0]['time'])
    t1 = _parse_time(rows[-1]['time'])
    duration_s = (t1 - t0) if (t0 is not None and t1 is not None) else 0.0

    # RF efficiency: mean((fwd - refl) / fwd), skip rows where fwd == 0
    rf_effs = []
    for r in rows:
        fwd = r['rf_fwd_W']
        if fwd > 0:
            rf_effs.append((fwd - r['rf_refl_W']) / fwd)
    rf_efficiency = sum(rf_effs) / len(rf_effs) if rf_effs else 0.0

    return {
        'peak_target_uA': max(targets),
        'avg_target_uA': sum(targets) / len(targets),
        'total_muAh': total_muAh,
        'duration_s': float(duration_s),
        'foil_no': rows[-1]['foil_no'],
        'avg_arc_I': sum(arc_Is) / len(arc_Is),
        'avg_vacuum_P': sum(vacuums) / len(vacuums),
        'peak_vacuum_P': max(vacuums),
        'rf_efficiency': rf_efficiency,
    }


def parse_log(text: str) -> dict:
    """Parse a complete PETrace log file. Returns header + summary + row_count."""
    header = parse_header(text)
    rows = parse_rows(text)
    stats = summarise(rows)
    return {**header, **stats, 'row_count': len(rows)}
