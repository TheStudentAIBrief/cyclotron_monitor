import numpy as np
import pandas as pd
from pathlib import Path

_MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB


def parse_beam_file(path: str) -> pd.DataFrame:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing to parse symlink: {path.name}")
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError(f"File exceeds size limit ({path.stat().st_size} bytes): {path.name}")
    cols = None
    current_date = None
    rows = []

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip()
            if len(line) > 4096:
                continue
            if 'DATE,TIME' in line:
                cols = [c.strip().split(' /')[0].strip() for c in line.split(',')]
                continue
            if cols is None:
                continue

            parts = line.split(',')
            if len(parts) < len(cols):
                continue

            date_field = parts[0].strip()
            if date_field:
                current_date = date_field
            if current_date is None:
                continue

            try:
                ts = pd.to_datetime(
                    f"{current_date} {parts[1].strip()}",
                    format='%m/%d/%Y %H:%M:%S.%f', errors='coerce'
                )
                if pd.isnull(ts):
                    ts = pd.to_datetime(
                        f"{current_date} {parts[1].strip()}",
                        format='%m/%d/%Y %H:%M:%S', errors='coerce'
                    )
                if pd.isnull(ts):
                    continue
            except Exception:
                continue

            row = {'timestamp': ts}
            for i, col in enumerate(cols[2:], start=2):
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


def aggregate_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df['date'] = df['timestamp'].dt.date
    numeric_cols = [c for c in df.columns if c not in ('timestamp', 'date')]

    result = []
    for d, group in df.groupby('date'):
        row = {'date': d}
        total = len(group)
        max_nan_frac = 0.0
        for col in numeric_cols:
            vals = group[col].dropna()
            nan_frac = 1.0 - len(vals) / total if total > 0 else 1.0
            max_nan_frac = max(max_nan_frac, nan_frac)
            row[f'{col}_mean'] = float(vals.mean()) if len(vals) > 0 else np.nan
            row[f'{col}_std'] = float(vals.std()) if len(vals) > 0 else np.nan
            row[f'{col}_min'] = float(vals.min()) if len(vals) > 0 else np.nan
            row[f'{col}_max'] = float(vals.max()) if len(vals) > 0 else np.nan
            row[f'{col}_p10'] = float(vals.quantile(0.1)) if len(vals) > 0 else np.nan
            row[f'{col}_p90'] = float(vals.quantile(0.9)) if len(vals) > 0 else np.nan
        row['data_quality'] = 'sparse' if max_nan_frac > 0.5 else 'ok'
        result.append(row)

    return pd.DataFrame(result).set_index('date')
