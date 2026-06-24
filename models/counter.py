import re
import sqlite3
from datetime import date, timedelta

AVG_CYCLES = {'ION SOURCE': 46, 'FOILS': 78, 'BL1 Target 1': 51, 'BL2 Target 1': 56}
COUNTER_THRESHOLD = 9999.0
COMPONENT_KEYS = {
    'ION SOURCE': 'isc_amphrs', 'FOILS': 'bl1_foil1_uamphrs',
    'BL1 Target 1': 'bl1_targ1_uamphrs', 'BL2 Target 1': 'bl2_targ1_uamphrs',
}
_COUNTER_RE = re.compile(r'(\w+) lifetime counter ([0-9.]+) is over (\d+)')


def get_counter_days(component_label: str, db_path: str) -> tuple:
    conn = sqlite3.connect(db_path)
    today = date.today()
    avg_cycle = AVG_CYCLES.get(component_label, 60)
    comp_key = COMPONENT_KEYS.get(component_label, '')

    row = conn.execute(
        "SELECT MAX(date(timestamp)) FROM maintenance_events WHERE component_label=?",
        [component_label]
    ).fetchone()
    last_maint = row[0] if row and row[0] else None
    days_since = (today - date.fromisoformat(last_maint)).days if last_maint else None

    if comp_key:
        window_start = (today - timedelta(days=14)).isoformat()
        warnings = conn.execute(
            "SELECT timestamp, message FROM events WHERE code='11001' "
            "AND message LIKE ? AND date(timestamp)>=? ORDER BY timestamp",
            [f'%{comp_key}%', window_start]
        ).fetchall()
        conn.close()

        if len(warnings) >= 2:
            readings = []
            for _, msg in warnings:
                m = _COUNTER_RE.search(msg)
                if m:
                    readings.append(float(m.group(2)))
            if len(readings) >= 2:
                rate_per_hour = (readings[-1] - readings[0]) / max(1, len(readings) - 1)
                daily_rate = max(0.001, rate_per_hour * 24)
                days_remaining = (COUNTER_THRESHOLD - readings[-1]) / daily_rate
                return float(days_remaining), days_since
    else:
        conn.close()

    if days_since is not None:
        return float(avg_cycle - days_since), days_since
    return float(avg_cycle), None
