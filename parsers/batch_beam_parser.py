import re
from pathlib import Path

import numpy as np
import pandas as pd

_MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB
_DATE_RE = re.compile(r'Date:\s*([\d\- ]+)\s*$')


def is_batch_beam_file(path: str) -> bool:
    """Sniff the first line: batch-beam files start with 'Tracer:' and carry 'Batch no:'."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            first_line = f.readline()
    except OSError:
        return False
    return first_line.startswith('Tracer:') and 'Batch no:' in first_line


def parse_batch_beam_file(path: str) -> pd.DataFrame:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing to parse symlink: {path.name}")
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError(f"File exceeds size limit ({path.stat().st_size} bytes): {path.name}")

    cols = None
    file_date = None
    rows = []

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            if len(line) > 4096:
                continue

            if line.startswith('Tracer:'):
                m = _DATE_RE.search(line)
                if m:
                    date_str = m.group(1).replace(' ', '')
                    file_date = pd.to_datetime(date_str, format='%Y-%m-%d', errors='coerce')
                continue

            if line.startswith('Site name:'):
                continue

            if cols is None:
                if line.startswith('Time\t'):
                    cols = [c.strip() for c in line.split('\t')]
                continue

            if not line.strip():
                continue

            if file_date is None or pd.isnull(file_date):
                continue

            parts = line.split('\t')
            if len(parts) < len(cols):
                continue

            time_str = parts[0].strip()
            ts = pd.to_datetime(
                f"{file_date.date()} {time_str}", format='%Y-%m-%d %H:%M:%S', errors='coerce'
            )
            if pd.isnull(ts):
                continue

            row = {'timestamp': ts}
            for i, col in enumerate(cols[1:], start=1):
                if i < len(parts):
                    try:
                        row[col] = float(parts[i])
                    except (ValueError, TypeError):
                        row[col] = np.nan
                else:
                    row[col] = np.nan
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
