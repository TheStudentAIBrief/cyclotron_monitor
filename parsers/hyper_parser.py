import re
import numpy as np
import pandas as pd
from pathlib import Path

_WARN_CODE_RE = re.compile(r'^warning (\w+): (\w+): (.*)')
_ERR_CODE_RE = re.compile(r'\(Err ([0-9a-fA-F]+)\)')
_LIFETIME_RE = re.compile(r'(\w+) lifetime counter ([0-9.]+) is over (\d+)')
_DATE_FROM_FILE_RE = re.compile(r'_(\d{6})\.log$')
_OLD_TS_RE = re.compile(r'^(\d{2}:\d{2}:\d{2}), (.*)$')
_NEW_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}), (.*)$')
_SEVERITY_KW = ('debug', 'info', 'verbose', 'note', 'error')


def _date_from_filename(name: str):
    m = _DATE_FROM_FILE_RE.search(name)
    if m:
        d = m.group(1)
        return f"20{d[0:2]}-{d[2:4]}-{d[4:6]}"
    return None


def _parse_rest(rest: str):
    rest = rest.strip()
    if rest.startswith('ERROR:'):
        inner = rest[6:].strip()
        parts = inner.split(': ', 1)
        func = parts[0].strip()
        msg = parts[1].strip() if len(parts) > 1 else ''
        cm = _ERR_CODE_RE.search(msg)
        return 'error', cm.group(1) if cm else None, func, msg

    m = _WARN_CODE_RE.match(rest)
    if m:
        return 'warning', m.group(1), m.group(2), m.group(3)

    for kw in _SEVERITY_KW:
        prefix = f'{kw}: '
        if rest.lower().startswith(prefix):
            inner = rest[len(prefix):]
            parts = inner.split(': ', 1)
            return kw, None, parts[0], parts[1] if len(parts) > 1 else ''

    parts = rest.split(': ', 1)
    return 'info', None, parts[0], parts[1] if len(parts) > 1 else rest


def parse_hyper_file(path: str) -> pd.DataFrame:
    path = Path(path)
    file_date = _date_from_filename(path.name)
    rows = []

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip()
            if not line:
                continue
            mn = _NEW_TS_RE.match(line)
            mo = _OLD_TS_RE.match(line)
            if mn:
                date_str, time_str, rest = mn.group(1), mn.group(2), mn.group(3)
            elif mo and file_date:
                date_str, time_str, rest = file_date, mo.group(1), mo.group(2)
            else:
                continue

            try:
                ts = pd.to_datetime(f"{date_str} {time_str}")
            except Exception:
                continue

            severity, code, func, msg = _parse_rest(rest)
            rows.append({
                'timestamp': ts,
                'severity': severity,
                'code': code,
                'function': func,
                'message': msg,
                'source_file': path.name,
            })

    if not rows:
        return pd.DataFrame(
            columns=['timestamp', 'severity', 'code', 'function', 'message', 'source_file'])
    return pd.DataFrame(rows)


def extract_lifetime_warnings(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=['timestamp', 'component', 'counter_uah', 'threshold_uah'])
    w = df[df['code'] == '11001'].copy()
    rows = []
    for _, row in w.iterrows():
        m = _LIFETIME_RE.search(str(row.get('message', '')))
        if m:
            rows.append({
                'timestamp': row['timestamp'],
                'component': m.group(1),
                'counter_uah': float(m.group(2)),
                'threshold_uah': float(m.group(3)),
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=['timestamp', 'component', 'counter_uah', 'threshold_uah'])


def extract_valve_toggles(df: pd.DataFrame, channel: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=['date', 'channel', 'toggle_count'])
    mask = df['message'].str.contains(channel, na=False, regex=False)
    sub = df[mask].copy()
    if sub.empty:
        return pd.DataFrame(columns=['date', 'channel', 'toggle_count'])
    sub['date'] = sub['timestamp'].dt.date
    counts = sub.groupby('date').size().reset_index(name='toggle_count')
    counts['channel'] = channel
    return counts[['date', 'channel', 'toggle_count']]
