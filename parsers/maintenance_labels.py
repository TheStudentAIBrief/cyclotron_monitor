import logging
import os
import re
import pandas as pd
from pathlib import Path

_log = logging.getLogger('cyclotron.maintenance_labels')

_MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB

_RESET_RE = re.compile(r'setlifetime\s*\{["\']?(\w+)["\']?\s*:\s*0\}')
_ANCHOR = ("cmdAddToQueue", "CMD:")
_DATE_RE = re.compile(r'_(\d{6})\.log$')
_ISO_TS_RE = re.compile(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})')
_TIME_RE = re.compile(r'(\d{2}:\d{2}:\d{2})')

COMPONENT_NAMES = {
    "isc_amphrs": "ION SOURCE", "dp2_hrs": "DIFFUSION PUMP 2",
    "bl1_foil1_uamphrs": "BL1 Foil 1", "bl1_foil2_uamphrs": "BL1 Foil 2",
    "bl1_foil3_uamphrs": "BL1 Foil 3", "bl2_foil1_uamphrs": "BL2 Foil 1",
    "bl2_foil2_uamphrs": "BL2 Foil 2", "bl2_foil3_uamphrs": "BL2 Foil 3",
    "bl1_targ1_uamphrs": "BL1 Target 1", "bl1_targ2_uamphrs": "BL1 Target 2",
    "bl1_targ3_uamphrs": "BL1 Target 3", "bl1_targ4_uamphrs": "BL1 Target 4",
    "bl2_targ1_uamphrs": "BL2 Target 1", "bl2_targ2_uamphrs": "BL2 Target 2",
    "bl2_targ3_uamphrs": "BL2 Target 3", "bl2_targ4_uamphrs": "BL2 Target 4",
}


def extract_maintenance_events(log_dir: str) -> pd.DataFrame:
    log_dir = Path(log_dir)
    events, seen = [], set()

    for filename in sorted(os.listdir(log_dir)):
        if not filename.endswith('.log'):
            continue
        m = _DATE_RE.search(filename)
        file_date = f"20{m.group(1)[0:2]}-{m.group(1)[2:4]}-{m.group(1)[4:6]}" if m else None
        if not file_date:
            continue

        try:
            filepath = log_dir / filename
            if filepath.is_symlink():
                continue
            if filepath.stat().st_size > _MAX_FILE_BYTES:
                continue
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if 'setlifetime' not in line:
                        continue
                    if len(line) > 4096:
                        continue
                    if not any(a in line for a in _ANCHOR):
                        continue
                    rm = _RESET_RE.search(line)
                    if not rm:
                        continue
                    key_name = rm.group(1)

                    iso = _ISO_TS_RE.search(line)
                    if iso:
                        event_date, time_str = iso.group(1), iso.group(2)
                    else:
                        event_date = file_date
                        tm = _TIME_RE.search(line)
                        time_str = tm.group(1) if tm else '00:00:00'

                    dedup_key = (event_date, key_name)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    events.append({
                        'timestamp': pd.to_datetime(f"{event_date} {time_str}"),
                        'component_key': key_name,
                        'component_label': COMPONENT_NAMES.get(key_name, key_name.upper()),
                        'source_file': filename,
                    })
        except Exception as e:
            _log.warning('Error extracting maintenance events from %s: %s', filename, e)
            continue

    if not events:
        return pd.DataFrame(
            columns=['timestamp', 'component_key', 'component_label', 'source_file'])
    return pd.DataFrame(events).sort_values('timestamp').reset_index(drop=True)


def extract_from_file(filepath) -> list:
    """Extract maintenance reset events from a single log file.

    Returns a list of dicts with keys: timestamp (ISO string), component_key,
    component_label, source_file.  Used by the live watcher to pick up new
    maintenance events without re-scanning the entire log directory.
    """
    filepath = Path(filepath)
    if not filepath.name.endswith('.log'):
        return []
    if filepath.is_symlink():
        return []
    try:
        if filepath.stat().st_size > _MAX_FILE_BYTES:
            return []
    except OSError:
        return []

    filename = filepath.name
    m = _DATE_RE.search(filename)
    file_date = f"20{m.group(1)[0:2]}-{m.group(1)[2:4]}-{m.group(1)[4:6]}" if m else None
    if not file_date:
        return []

    events, seen = [], set()
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if 'setlifetime' not in line:
                    continue
                if len(line) > 4096:
                    continue
                if not any(a in line for a in _ANCHOR):
                    continue
                rm = _RESET_RE.search(line)
                if not rm:
                    continue
                key_name = rm.group(1)

                iso = _ISO_TS_RE.search(line)
                if iso:
                    event_date, time_str = iso.group(1), iso.group(2)
                else:
                    event_date = file_date
                    tm = _TIME_RE.search(line)
                    time_str = tm.group(1) if tm else '00:00:00'

                dedup_key = (event_date, key_name)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                events.append({
                    'timestamp': f"{event_date} {time_str}",
                    'component_key': key_name,
                    'component_label': COMPONENT_NAMES.get(key_name, key_name.upper()),
                    'source_file': filename,
                })
    except Exception as e:
        _log.warning('Error extracting maintenance events from %s: %s', filename, e)

    return events
