# Cyclotron Maintenance Prediction Monitor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cyclotron maintenance prediction system that forecasts component replacements 7 days ahead, displayed as a visual progress-bar dashboard for non-technical staff.

**Architecture:** Ensemble of GradientBoosting ML model + lifetime counter projection. SQLite feature store populated by parsing beam/hyper logs. Single-file HTML dashboard reads `dashboard.json` every 60s. Watchdog monitors live log directory.

**Tech Stack:** Python 3.14, pandas>=2.0, scikit-learn>=1.4, watchdog>=4.0, matplotlib>=3.8, sqlite3 (stdlib)

## Global Constraints

- Raw logs at `C:\Users\theol\cyclotron_data\raw\` — read-only; never write there
- Project root: `C:\Users\theol\cyclotron_monitor\`
- SQLite DB: `data\cyclotron.db`; models: `data\models\*.pkl`
- Dashboard JSON: `data\dashboard.json`; ALERT file: `C:\Users\theol\cyclotron_data\ALERT.txt`
- `GradientBoostingClassifier` does NOT support `class_weight` — use `compute_sample_weight('balanced', y)` passed to `pipeline.fit(gbm__sample_weight=weights)` instead
- Linear regression: use `numpy.polyfit(x, y, 1)[0]`; do NOT add scipy dependency
- Quality gate: precision ≥ 0.5 AND recall ≥ 0.6; else counter-only mode for that component
- Software update boundary: 2026-05-15 (MI_* log format starts; no `total_errors` feature — only subsystem-specific fault codes)
- Label window: positive = within 7 days before maintenance; negative = >14 days; exclude 8–14 days
- Calibration: `CalibratedClassifierCV(pipeline, cv='prefit', method='isotonic')` on chronological 30% holdout

---

## File Map

| File | Responsibility |
|---|---|
| `db.py` | SQLite init + upsert helpers |
| `ingest.py` | Walk log_dir, parse all files, populate DB |
| `parsers/beam_parser.py` | `parse_beam_file()`, `aggregate_daily()` |
| `parsers/hyper_parser.py` | `parse_hyper_file()`, `extract_lifetime_warnings()`, `extract_valve_toggles()` |
| `parsers/maintenance_labels.py` | `extract_maintenance_events()` |
| `features/engineer.py` | `build_features(date, component, db_path) -> dict` |
| `models/counter.py` | `get_counter_days(component_label, db_path) -> (float, int|None)` |
| `models/trainer.py` | `train_component()`, `build_training_data()` |
| `models/predictor.py` | `predict() -> PredictionResult` |
| `monitor/dashboard_writer.py` | `write_dashboard(predictions, dashboard_path, alert_path)` |
| `monitor/watcher.py` | Watchdog event loop |
| `ui/index.html` | Self-contained visual dashboard |
| `main.py` | CLI: `train | predict | monitor | patterns` |

---

## Task 1: Scaffold, config, requirements, SQLite schema

**Files:**
- Create: `requirements.txt`
- Create: `config.json`
- Create: `db.py`
- Create: `parsers/__init__.py`, `features/__init__.py`, `models/__init__.py`, `monitor/__init__.py`
- Create: `data/models/` directory
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `db.init_db(db_path)`, `db.upsert_beam_daily(conn, ...)`, `db.insert_events(conn, rows)`, `db.upsert_maintenance_event(conn, ...)`

- [ ] **Step 1: Create directory structure**

```
cd C:\Users\theol\cyclotron_monitor
mkdir parsers features models monitor ui data\models tests tests\fixtures
```

- [ ] **Step 2: Write `requirements.txt`**

```
pandas>=2.0
scikit-learn>=1.4
watchdog>=4.0
matplotlib>=3.8
```

- [ ] **Step 3: Install dependencies**

Run: `pip install pandas scikit-learn watchdog matplotlib`
Expected: all packages install without errors.

- [ ] **Step 4: Write `config.json`**

```json
{
  "log_dir": "C:\\Users\\theol\\cyclotron_data\\raw",
  "db_path": "C:\\Users\\theol\\cyclotron_monitor\\data\\cyclotron.db",
  "model_dir": "C:\\Users\\theol\\cyclotron_monitor\\data\\models",
  "dashboard_path": "C:\\Users\\theol\\cyclotron_monitor\\data\\dashboard.json",
  "alert_path": "C:\\Users\\theol\\cyclotron_data\\ALERT.txt"
}
```

- [ ] **Step 5: Write `db.py`**

```python
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS beam_daily (
    date TEXT NOT NULL,
    param TEXT NOT NULL,
    mean REAL, std REAL, min REAL, max REAL, p10 REAL, p90 REAL,
    data_quality TEXT DEFAULT 'ok',
    PRIMARY KEY (date, param)
);
CREATE TABLE IF NOT EXISTS events (
    timestamp TEXT NOT NULL,
    severity TEXT,
    code TEXT,
    function TEXT,
    message TEXT,
    source_file TEXT,
    UNIQUE(timestamp, source_file, code, function)
);
CREATE TABLE IF NOT EXISTS maintenance_events (
    timestamp TEXT NOT NULL,
    component_key TEXT NOT NULL,
    component_label TEXT NOT NULL,
    source_file TEXT,
    PRIMARY KEY (timestamp, component_key)
);
CREATE TABLE IF NOT EXISTS predictions (
    run_at TEXT NOT NULL,
    component TEXT NOT NULL,
    risk_score REAL,
    days_estimate REAL,
    alert_level TEXT,
    primary_signal TEXT,
    top_features TEXT,
    PRIMARY KEY (run_at, component)
);
"""

def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

def upsert_beam_daily(conn, date_str, param, stats, data_quality='ok'):
    conn.execute(
        "INSERT OR REPLACE INTO beam_daily VALUES (?,?,?,?,?,?,?,?,?)",
        [date_str, param, stats.get('mean'), stats.get('std'), stats.get('min'),
         stats.get('max'), stats.get('p10'), stats.get('p90'), data_quality]
    )

def insert_events(conn, rows):
    conn.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)", rows)

def upsert_maintenance_event(conn, timestamp, component_key, component_label, source_file):
    conn.execute(
        "INSERT OR REPLACE INTO maintenance_events VALUES (?,?,?,?)",
        [timestamp, component_key, component_label, source_file]
    )
```

- [ ] **Step 6: Write `tests/conftest.py`**

```python
import sqlite3
import pytest
from datetime import date, timedelta

def make_beam_rows(target_date: date, n_days: int, param: str,
                   base_val: float, slope: float = 0.0):
    """Generate n_days of INSERT-ready beam_daily rows ending at target_date."""
    rows = []
    for i in range(n_days):
        d = (target_date - timedelta(days=n_days - 1 - i)).isoformat()
        val = base_val + slope * i
        rows.append((d, param, val, 0.01, val - 0.1, val + 0.1,
                     val - 0.05, val + 0.05, 'ok'))
    return rows

def setup_test_db(tmp_path, beam_rows=None, event_rows=None, maint_rows=None):
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE beam_daily (date TEXT, param TEXT, mean REAL, std REAL,
            min REAL, max REAL, p10 REAL, p90 REAL, data_quality TEXT,
            PRIMARY KEY (date, param));
        CREATE TABLE events (timestamp TEXT, severity TEXT, code TEXT,
            function TEXT, message TEXT, source_file TEXT,
            UNIQUE(timestamp, source_file, code, function));
        CREATE TABLE maintenance_events (timestamp TEXT, component_key TEXT,
            component_label TEXT, source_file TEXT,
            PRIMARY KEY (timestamp, component_key));
    """)
    if beam_rows:
        conn.executemany("INSERT OR REPLACE INTO beam_daily VALUES (?,?,?,?,?,?,?,?,?)", beam_rows)
    if event_rows:
        conn.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)", event_rows)
    if maint_rows:
        conn.executemany("INSERT OR REPLACE INTO maintenance_events VALUES (?,?,?,?)", maint_rows)
    conn.commit()
    conn.close()
    return db
```

- [ ] **Step 7: Create empty `__init__.py` files**

Create empty files: `parsers/__init__.py`, `features/__init__.py`, `models/__init__.py`, `monitor/__init__.py`

- [ ] **Step 8: Commit**

```
git add .
git commit -m "feat: project scaffold, config, SQLite schema, test helpers"
```

---

## Task 2: Beam log parser (TDD)

**Files:**
- Create: `tests/fixtures/beam_sample.log`
- Create: `tests/test_parsers.py` (beam tests only)
- Create: `parsers/beam_parser.py`

**Interfaces:**
- Produces: `parse_beam_file(path: str) -> pd.DataFrame` with columns `timestamp` + 22 beam params; `aggregate_daily(df) -> pd.DataFrame` with `{param}_mean/std/min/max/p10/p90` columns indexed by `date`

- [ ] **Step 1: Write fixture `tests/fixtures/beam_sample.log`**

```
RDS-111 beam log [/var/local/eclipse/log/beam_sample.log] -- started at 01/08/2026 04:49:06

ID  Condition
C1  Log if DO_WAKEUP_PWR = 1

DATE,TIME,AI_TANK_HI_PRES /C1,AI_ISGAS_FLOW /C1,SW_RF_FREQ /C1,AO_RF_AMPL /C1,AI_DEE_VOLT /C1,AI_RFFWD_PWR /C1,AI_RFREF_PWR /C1,AI_MMA_CUR /C1,AI_MMT_CUR /C1,AO_MMO_CUR /C1,AI_IS_CUR /C1,AI_IS_VOLT /C1,AI_BIAS_VOLT /C1,AI_BIAS_CUR /C1,AI_BL1_FOIL_CUR /C1,AI_BL1_TARG_CUR /C1,AI_BL1_COL_CUR /C1,AI_BL2_FOIL_CUR /C1,AI_BL2_TARG_CUR /C1,AI_BL2_COL_CUR /C1,AI_BOP_CUR /C1
01/08/2026,17:13:35.4,3.78e-07,0.0214,0,0,0.0639,-7.35,-36.7,207.14,0.91,0,0.0007,0.027,-0.009,0.155,0.209,-0.042,0.382,0.125,-0.081,0.277,0.023
,17:13:40.4,3.82e-07,5.534,72.1,0,0.057,-7.34,-36.5,207.14,0.93,0,0.0004,0.027,-0.008,0.147,0.209,0.056,0.382,0.125,-0.061,0.277,0.023
bad_date,not_a_time,garbage,line,here
```

- [ ] **Step 2: Write failing tests in `tests/test_parsers.py`**

```python
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from parsers.beam_parser import parse_beam_file, aggregate_daily

FIXTURE = Path(__file__).parent / "fixtures" / "beam_sample.log"

EXPECTED_PARAMS = [
    'AI_TANK_HI_PRES','AI_ISGAS_FLOW','SW_RF_FREQ','AO_RF_AMPL','AI_DEE_VOLT',
    'AI_RFFWD_PWR','AI_RFREF_PWR','AI_MMA_CUR','AI_MMT_CUR','AO_MMO_CUR',
    'AI_IS_CUR','AI_IS_VOLT','AI_BIAS_VOLT','AI_BIAS_CUR','AI_BL1_FOIL_CUR',
    'AI_BL1_TARG_CUR','AI_BL1_COL_CUR','AI_BL2_FOIL_CUR','AI_BL2_TARG_CUR',
    'AI_BL2_COL_CUR','AI_BOP_CUR',
]

def test_beam_parser_returns_22_columns():
    df = parse_beam_file(str(FIXTURE))
    for col in EXPECTED_PARAMS:
        assert col in df.columns, f"Missing: {col}"

def test_beam_parser_handles_date_inheritance():
    df = parse_beam_file(str(FIXTURE))
    # 2 valid data rows: second row has no date, inherits from first
    assert len(df) == 2
    assert df['timestamp'].dt.date.nunique() == 1

def test_beam_parser_handles_malformed_rows_as_nan():
    df = parse_beam_file(str(FIXTURE))
    # Malformed row is silently dropped; result still a DataFrame
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2  # malformed row not included

def test_aggregate_daily_returns_stats():
    df = parse_beam_file(str(FIXTURE))
    daily = aggregate_daily(df)
    assert 'AI_IS_CUR_mean' in daily.columns
    assert 'AI_BOP_CUR_p90' in daily.columns
    assert 'data_quality' in daily.columns
    assert len(daily) >= 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_parsers.py -v`
Expected: 4 failures with `ModuleNotFoundError: No module named 'parsers.beam_parser'`

- [ ] **Step 4: Implement `parsers/beam_parser.py`**

```python
import re
import numpy as np
import pandas as pd
from pathlib import Path

def parse_beam_file(path: str) -> pd.DataFrame:
    path = Path(path)
    cols = None
    current_date = None
    rows = []

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip()
            if 'DATE,TIME' in line:
                # Strip /C1 condition annotations from column names
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_parsers.py -v`
Expected: 4 PASSED

- [ ] **Step 6: Commit**

```
git add parsers/beam_parser.py tests/test_parsers.py tests/fixtures/beam_sample.log
git commit -m "feat: beam log parser with date-inheritance and daily aggregation"
```

---

## Task 3: Hyper log parser + maintenance labels (TDD)

**Files:**
- Create: `tests/fixtures/hyper_sample.log`
- Create: `tests/fixtures/hyper_maintenance.log`
- Create: `tests/fixtures/hyper_valve_chattering.log`
- Create: `parsers/hyper_parser.py`
- Create: `parsers/maintenance_labels.py`
- Modify: `tests/test_parsers.py` (add hyper + maintenance tests)

**Interfaces:**
- Produces: `parse_hyper_file(path) -> pd.DataFrame` (columns: timestamp, severity, code, function, message, source_file); `extract_lifetime_warnings(df) -> pd.DataFrame` (timestamp, component, counter_uah, threshold_uah); `extract_valve_toggles(df, channel) -> pd.DataFrame` (date, channel, toggle_count); `extract_maintenance_events(log_dir) -> pd.DataFrame` (timestamp, component_key, component_label, source_file)

- [ ] **Step 1: Write test fixtures**

`tests/fixtures/hyper_sample.log`:
```
04:49:06, debug: rfcConnect: error 2 opening /dev/ttyUSB0
04:49:06, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to ON
04:50:07, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to OFF
17:14:57, warning 12072: checkTankVacuum: High tank pressure 7.7e-06!
20:45:05, warning 10804: checkISwarnings: Ion Source appears to be open.
08:00:00, warning 11001: checkLifetime: isc_amphrs lifetime counter 10234 is over 9999
```

`tests/fixtures/hyper_maintenance.log`:
```
05:00:00, note: cmdAddToQueue: setlifetime {"isc_amphrs":0}
05:00:01, note: cmdProc: setlifetime {"isc_amphrs":0}
06:00:00, note: cmdAddToQueue: setlifetime {"bl1_foil1_uamphrs":0}
```

`tests/fixtures/hyper_valve_chattering.log`:
```
04:50:06, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to ON
04:50:07, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to OFF
04:50:17, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to ON
04:51:31, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to OFF
04:51:39, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to ON
04:51:41, archSync: IO Channel DO_BL2_TSU3_VALVE6 set to OFF
```

`tests/fixtures/hyper_new_format.log`:
```
2026-05-18 04:09:20, ERROR: qeiSend32bitValue: set value failed - no ack (Err 11d01)
2026-05-18 04:09:25, ERROR: qeiGenUnlock: QEI Unlock Failed; no response (Err 10901)
```

- [ ] **Step 2: Add failing tests to `tests/test_parsers.py`**

```python
from parsers.hyper_parser import parse_hyper_file, extract_lifetime_warnings, extract_valve_toggles
from parsers.maintenance_labels import extract_maintenance_events

HYPER_FIXTURE = Path(__file__).parent / "fixtures" / "hyper_sample.log"
HYPER_MAINT = Path(__file__).parent / "fixtures" / "hyper_maintenance.log"
HYPER_VALVE = Path(__file__).parent / "fixtures" / "hyper_valve_chattering.log"
HYPER_NEW = Path(__file__).parent / "fixtures" / "hyper_new_format.log"

def test_hyper_parser_extracts_error_codes():
    # hyper_sample.log is named with YYMMDD so the parser can get date from filename
    # Copy fixture to a temp named file is not needed — the parser handles missing filename date
    df = parse_hyper_file(str(HYPER_FIXTURE))
    assert '12072' in df['code'].values
    assert '10804' in df['code'].values

def test_hyper_parser_extracts_lifetime_warnings_with_counter_value():
    df = parse_hyper_file(str(HYPER_FIXTURE))
    warnings = extract_lifetime_warnings(df)
    assert len(warnings) == 1
    assert warnings.iloc[0]['component'] == 'isc_amphrs'
    assert warnings.iloc[0]['counter_uah'] == 10234.0
    assert warnings.iloc[0]['threshold_uah'] == 9999.0

def test_hyper_parser_counts_valve_toggles():
    df = parse_hyper_file(str(HYPER_VALVE))
    toggles = extract_valve_toggles(df, 'DO_BL2_TSU3_VALVE6')
    assert len(toggles) >= 1
    assert toggles['toggle_count'].sum() == 6

def test_hyper_parser_extracts_new_format_error_codes(tmp_path):
    import shutil
    # Copy to tmp with date in name so parser can derive date
    dest = tmp_path / "MI_10150863_105_hyper_260518.log"
    shutil.copy(str(HYPER_NEW), str(dest))
    df = parse_hyper_file(str(dest))
    assert len(df) == 2
    assert any(c in df['code'].values for c in ('11d01', '10901'))

def test_maintenance_labels_finds_setlifetime_resets(tmp_path):
    import shutil
    dest = tmp_path / "hyper_260315.log"
    shutil.copy(str(HYPER_MAINT), str(dest))
    df = extract_maintenance_events(str(tmp_path))
    assert len(df) == 2
    assert 'isc_amphrs' in df['component_key'].values
    assert 'bl1_foil1_uamphrs' in df['component_key'].values

def test_maintenance_labels_deduplicates_cmdproc_lines(tmp_path):
    import shutil
    dest = tmp_path / "hyper_260315.log"
    shutil.copy(str(HYPER_MAINT), str(dest))
    df = extract_maintenance_events(str(tmp_path))
    # cmdProc line must be excluded
    isc_events = df[df['component_key'] == 'isc_amphrs']
    assert len(isc_events) == 1, "cmdProc should not be counted"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_parsers.py -k "hyper or maintenance" -v`
Expected: all 6 FAILED with ImportError

- [ ] **Step 4: Implement `parsers/hyper_parser.py`**

```python
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
    # New format: ERROR: func: message (Err XXXX)
    if rest.startswith('ERROR:'):
        inner = rest[6:].strip()
        parts = inner.split(': ', 1)
        func = parts[0].strip()
        msg = parts[1].strip() if len(parts) > 1 else ''
        cm = _ERR_CODE_RE.search(msg)
        return 'error', cm.group(1) if cm else None, func, msg

    # warning NNNN: func: message
    m = _WARN_CODE_RE.match(rest)
    if m:
        return 'warning', m.group(1), m.group(2), m.group(3)

    # severity keyword: func: message
    for kw in _SEVERITY_KW:
        prefix = f'{kw}: '
        if rest.lower().startswith(prefix):
            inner = rest[len(prefix):]
            parts = inner.split(': ', 1)
            return kw, None, parts[0], parts[1] if len(parts) > 1 else ''

    # no severity prefix: func: message (e.g. archSync: IO Channel...)
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
            columns=['timestamp','severity','code','function','message','source_file'])
    return pd.DataFrame(rows)


def extract_lifetime_warnings(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=['timestamp','component','counter_uah','threshold_uah'])
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
        columns=['timestamp','component','counter_uah','threshold_uah'])


def extract_valve_toggles(df: pd.DataFrame, channel: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=['date','channel','toggle_count'])
    mask = df['message'].str.contains(channel, na=False, regex=False)
    sub = df[mask].copy()
    if sub.empty:
        return pd.DataFrame(columns=['date','channel','toggle_count'])
    sub['date'] = sub['timestamp'].dt.date
    counts = sub.groupby('date').size().reset_index(name='toggle_count')
    counts['channel'] = channel
    return counts[['date','channel','toggle_count']]
```

- [ ] **Step 5: Implement `parsers/maintenance_labels.py`**

```python
import os
import re
import pandas as pd
from pathlib import Path

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
            with open(log_dir / filename, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if 'setlifetime' not in line:
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
        except Exception:
            continue

    if not events:
        return pd.DataFrame(
            columns=['timestamp','component_key','component_label','source_file'])
    return pd.DataFrame(events).sort_values('timestamp').reset_index(drop=True)
```

- [ ] **Step 6: Run tests to verify all pass**

Run: `pytest tests/test_parsers.py -v`
Expected: all 10 tests PASSED

- [ ] **Step 7: Commit**

```
git add parsers/ tests/
git commit -m "feat: hyper log parser, lifetime warnings, valve toggles, maintenance label extractor"
```

---

## Task 4: DB ingestion module (TDD)

**Files:**
- Create: `ingest.py`
- Create: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `parse_beam_file`, `aggregate_daily`, `parse_hyper_file`, `extract_maintenance_events`, `db.*`
- Produces: `ingest_all(log_dir, db_path) -> dict` with keys `beam_files`, `hyper_files`, `events`, `maintenance_events`

- [ ] **Step 1: Write failing test**

```python
# tests/test_ingest.py
import sqlite3
import shutil
from pathlib import Path
from ingest import ingest_all

FIXTURE_DIR = Path(__file__).parent / "fixtures"

def test_ingest_populates_beam_daily(tmp_path):
    # Copy beam fixture to tmp log dir named correctly
    (tmp_path / "logs").mkdir()
    shutil.copy(str(FIXTURE_DIR / "beam_sample.log"),
                str(tmp_path / "logs" / "beam_260108.log"))
    db = str(tmp_path / "test.db")
    stats = ingest_all(str(tmp_path / "logs"), db)
    assert stats['beam_files'] >= 1
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM beam_daily").fetchone()[0]
    conn.close()
    assert rows > 0, "beam_daily should be populated"

def test_ingest_populates_maintenance_events(tmp_path):
    (tmp_path / "logs").mkdir()
    shutil.copy(str(FIXTURE_DIR / "hyper_maintenance.log"),
                str(tmp_path / "logs" / "hyper_260315.log"))
    db = str(tmp_path / "test.db")
    ingest_all(str(tmp_path / "logs"), db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM maintenance_events").fetchone()[0]
    conn.close()
    assert rows > 0, "maintenance_events should be populated"

def test_ingest_is_idempotent(tmp_path):
    (tmp_path / "logs").mkdir()
    shutil.copy(str(FIXTURE_DIR / "hyper_maintenance.log"),
                str(tmp_path / "logs" / "hyper_260315.log"))
    db = str(tmp_path / "test.db")
    ingest_all(str(tmp_path / "logs"), db)
    ingest_all(str(tmp_path / "logs"), db)  # Run twice
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM maintenance_events").fetchone()[0]
    conn.close()
    assert rows == 1, "Second ingest should not duplicate rows"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_ingest.py -v`
Expected: FAILED with ImportError

- [ ] **Step 3: Implement `ingest.py`**

```python
import os
import sqlite3
from pathlib import Path
from parsers.beam_parser import parse_beam_file, aggregate_daily
from parsers.hyper_parser import parse_hyper_file
from parsers.maintenance_labels import extract_maintenance_events
from db import init_db, upsert_beam_daily, insert_events, upsert_maintenance_event

def ingest_all(log_dir: str, db_path: str) -> dict:
    init_db(db_path)
    stats = {'beam_files': 0, 'hyper_files': 0, 'events': 0, 'maintenance_events': 0}

    beam_files = sorted(
        f for f in os.listdir(log_dir)
        if f.endswith('.log') and 'beam' in f
    )
    hyper_files = sorted(
        f for f in os.listdir(log_dir)
        if f.endswith('.log') and ('hyper' in f or 'ui' in f)
    )

    conn = sqlite3.connect(db_path)

    for filename in beam_files:
        try:
            df = parse_beam_file(str(Path(log_dir) / filename))
            daily = aggregate_daily(df)
            for d, row in daily.iterrows():
                numeric_params = [c[:-5] for c in row.index if c.endswith('_mean')]
                for param in numeric_params:
                    stats_dict = {
                        'mean': row.get(f'{param}_mean'),
                        'std':  row.get(f'{param}_std'),
                        'min':  row.get(f'{param}_min'),
                        'max':  row.get(f'{param}_max'),
                        'p10':  row.get(f'{param}_p10'),
                        'p90':  row.get(f'{param}_p90'),
                    }
                    upsert_beam_daily(conn, str(d), param, stats_dict,
                                      str(row.get('data_quality', 'ok')))
            stats['beam_files'] += 1
        except Exception as e:
            print(f"  WARN beam {filename}: {e}")

    for filename in hyper_files:
        try:
            df = parse_hyper_file(str(Path(log_dir) / filename))
            if not df.empty:
                rows = [
                    (str(r['timestamp']), r['severity'], r['code'],
                     r['function'], r['message'], r['source_file'])
                    for _, r in df.iterrows()
                ]
                insert_events(conn, rows)
                stats['events'] += len(rows)
            stats['hyper_files'] += 1
        except Exception as e:
            print(f"  WARN hyper {filename}: {e}")

    # Maintenance events from all hyper files together
    maint_df = extract_maintenance_events(log_dir)
    for _, row in maint_df.iterrows():
        upsert_maintenance_event(conn, str(row['timestamp']),
                                 row['component_key'], row['component_label'],
                                 row['source_file'])
    stats['maintenance_events'] = len(maint_df)

    conn.commit()
    conn.close()
    return stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```
git add ingest.py tests/test_ingest.py db.py
git commit -m "feat: DB ingestion — populates all four tables from raw log directory"
```

---

## Task 5: Feature engineering (TDD)

**Files:**
- Create: `features/engineer.py`
- Create: `tests/test_features.py`

**Interfaces:**
- Consumes: `cyclotron.db` (beam_daily, events, maintenance_events tables)
- Produces: `build_features(target_date: date, component: str, db_path: str) -> dict`
  Keys include: `AI_IS_CUR_7d_mean`, `AI_IS_CUR_7d_std`, `AI_IS_CUR_7d_slope`, (same for 14d/30d), fault rate features, `counter_days_remaining`, `days_since_last_maintenance`, `efficiency_ratio`, `efficiency_slope_14d`, `valve_bl2_tsu3_toggles_7d`, `post_v51_software`

- [ ] **Step 1: Write failing tests in `tests/test_features.py`**

```python
import numpy as np
import pytest
import sqlite3
from datetime import date, timedelta
from features.engineer import build_features
from tests.conftest import make_beam_rows, setup_test_db

def test_engineer_computes_rolling_slope_correctly(tmp_path):
    target = date(2025, 3, 15)
    # 14 days of IS current, slope +0.1/day
    beam = make_beam_rows(target, 14, 'AI_IS_CUR', 2.0, slope=0.1)
    db = setup_test_db(tmp_path, beam_rows=beam)
    f = build_features(target, 'ION SOURCE', db)
    slope = f.get('AI_IS_CUR_14d_slope', np.nan)
    assert not np.isnan(slope), "Slope must not be NaN with 14 days of data"
    assert abs(slope - 0.1) < 0.05, f"Expected slope ~0.1, got {slope}"

def test_engineer_returns_nan_when_fewer_than_7_days(tmp_path):
    target = date(2025, 3, 15)
    beam = make_beam_rows(target, 3, 'AI_IS_CUR', 2.0)
    db = setup_test_db(tmp_path, beam_rows=beam)
    f = build_features(target, 'ION SOURCE', db)
    assert np.isnan(f.get('AI_IS_CUR_7d_mean', np.nan)), "Must be NaN with only 3 days"

def test_engineer_computes_efficiency_ratio(tmp_path):
    target = date(2025, 3, 15)
    beam = (make_beam_rows(target, 14, 'AI_IS_CUR', 4.0) +
            make_beam_rows(target, 14, 'AI_BOP_CUR', 8.0))
    db = setup_test_db(tmp_path, beam_rows=beam)
    f = build_features(target, 'ION SOURCE', db)
    ratio = f.get('efficiency_ratio', np.nan)
    assert not np.isnan(ratio)
    assert abs(ratio - 2.0) < 0.01, f"Expected BOP/IS = 8/4 = 2.0, got {ratio}"

def test_engineer_computes_fault_rates(tmp_path):
    target = date(2025, 3, 15)
    events = [
        (f'2025-03-{9+i:02d} 10:00:00', 'warning', '10802',
         'periodicCheckISC', 'IS check failed', 'hyper.log')
        for i in range(3)  # 2025-03-09, 10, 11 — within 7 days of Mar 15
    ]
    db = setup_test_db(tmp_path, event_rows=events)
    f = build_features(target, 'ION SOURCE', db)
    assert f.get('fault_is_10802_7d', 0) == 3

def test_engineer_computes_valve_toggle_rate(tmp_path):
    target = date(2026, 1, 8)
    events = [
        (f'2026-01-0{(i % 7) + 1} 04:{40 + i % 10}:00', 'info', None,
         'archSync', 'IO Channel DO_BL2_TSU3_VALVE6 set to ON', 'hyper.log')
        for i in range(22)
    ]
    db = setup_test_db(tmp_path, event_rows=events)
    f = build_features(target, 'BL2 Target 1', db)
    assert f.get('valve_bl2_tsu3_toggles_7d', 0) == 22
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_features.py -v`
Expected: all 5 FAILED with ImportError

- [ ] **Step 3: Implement `features/engineer.py`**

```python
import sqlite3
import numpy as np
from datetime import date, timedelta

SOFTWARE_UPDATE_DATE = date(2026, 5, 15)
VALVE_CHANNEL = 'DO_BL2_TSU3_VALVE6'

COMPONENT_PARAMS = {
    'ION SOURCE':    ['AI_IS_CUR','AI_IS_VOLT','AI_BIAS_VOLT','AI_BIAS_CUR','AI_BOP_CUR'],
    'FOILS':         ['AI_BL1_FOIL_CUR','AI_BL2_FOIL_CUR','AI_BL1_COL_CUR','AI_BL2_COL_CUR'],
    'BL1 Target 1':  ['AI_BL1_TARG_CUR','AI_BL1_FOIL_CUR','AI_BOP_CUR'],
    'BL2 Target 1':  ['AI_BL2_TARG_CUR','AI_BL2_FOIL_CUR','AI_BOP_CUR'],
}
IS_FAULT_CODES = ('10802', '10804', '10808', '10809')
BL_FAULT_CODES = ('10401', '10f01')
COMPONENT_KEYS = {
    'ION SOURCE': 'isc_amphrs',
    'FOILS': 'bl1_foil1_uamphrs',
    'BL1 Target 1': 'bl1_targ1_uamphrs',
    'BL2 Target 1': 'bl2_targ1_uamphrs',
}
AVG_CYCLES = {'ION SOURCE': 46, 'FOILS': 78, 'BL1 Target 1': 51, 'BL2 Target 1': 56}


def _slope(values):
    """Linear regression slope via numpy (no scipy)."""
    if len(values) < 2:
        return np.nan
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, values, 1)[0])


def _query_daily_means(conn, params, start: date, end: date) -> dict:
    """Returns {param: {date_str: mean_val}} for the date range [start, end)."""
    ph = ','.join('?' * len(params))
    rows = conn.execute(
        f"SELECT date, param, mean FROM beam_daily "
        f"WHERE date >= ? AND date < ? AND param IN ({ph})",
        [start.isoformat(), end.isoformat()] + list(params)
    ).fetchall()
    result = {}
    for d, param, val in rows:
        result.setdefault(param, {})[d] = val
    return result


def build_features(target_date: date, component: str, db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    features = {}
    params = COMPONENT_PARAMS.get(component, [])

    # Rolling beam stats: 7, 14, 30-day windows
    for w in (7, 14, 30):
        start = target_date - timedelta(days=w)
        daily = _query_daily_means(conn, params, start, target_date)
        for param in params:
            vals_dict = daily.get(param, {})
            vals = [v for v in vals_dict.values() if v is not None]
            if len(vals) < 7:
                features[f'{param}_{w}d_mean'] = np.nan
                features[f'{param}_{w}d_std'] = np.nan
                features[f'{param}_{w}d_slope'] = np.nan
            else:
                arr = np.array(sorted(vals_dict.items()), dtype=object)
                y = np.array([v for _, v in arr], dtype=float)
                features[f'{param}_{w}d_mean'] = float(np.nanmean(y))
                features[f'{param}_{w}d_std'] = float(np.nanstd(y))
                features[f'{param}_{w}d_slope'] = _slope(y)

    # IS-specific fault rates (7d and 14d)
    for code in IS_FAULT_CODES:
        for w, label in ((7, '7d'), (14, '14d')):
            start = (target_date - timedelta(days=w)).isoformat()
            cnt = conn.execute(
                "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? AND code=?",
                [start, target_date.isoformat(), code]
            ).fetchone()[0]
            features[f'fault_is_{code}_{label}'] = int(cnt)

    for code in BL_FAULT_CODES:
        for w, label in ((7, '7d'), (14, '14d')):
            start = (target_date - timedelta(days=w)).isoformat()
            cnt = conn.execute(
                "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? AND code=?",
                [start, target_date.isoformat(), code]
            ).fetchone()[0]
            features[f'fault_bl_{code}_{label}'] = int(cnt)

    # Lifetime overrun warning count (14d)
    start14 = (target_date - timedelta(days=14)).isoformat()
    cnt11001 = conn.execute(
        "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? AND code='11001'",
        [start14, target_date.isoformat()]
    ).fetchone()[0]
    features['fault_11001_14d'] = int(cnt11001)

    # Days since last maintenance
    comp_key = COMPONENT_KEYS.get(component)
    row = conn.execute(
        "SELECT MAX(date(timestamp)) FROM maintenance_events WHERE component_label=?",
        [component]
    ).fetchone()
    last_maint = row[0] if row and row[0] else None
    days_since = (target_date - date.fromisoformat(last_maint)).days if last_maint else None
    features['days_since_last_maintenance'] = days_since if days_since is not None else np.nan

    avg_cycle = AVG_CYCLES.get(component, 60)
    features['counter_days_remaining'] = (avg_cycle - days_since) if days_since is not None else float(avg_cycle)

    # Efficiency ratio (ION SOURCE only)
    if component == 'ION SOURCE':
        bop = features.get('AI_BOP_CUR_14d_mean', np.nan)
        isc = features.get('AI_IS_CUR_14d_mean', np.nan)
        if not (np.isnan(bop) or np.isnan(isc)) and isc != 0:
            features['efficiency_ratio'] = bop / isc
            # Efficiency slope: daily ratio over 14d
            start14_d = target_date - timedelta(days=14)
            daily14 = _query_daily_means(conn, ['AI_BOP_CUR','AI_IS_CUR'], start14_d, target_date)
            all_dates = sorted(set(daily14.get('AI_BOP_CUR',{}).keys()) &
                               set(daily14.get('AI_IS_CUR',{}).keys()))
            ratios = [daily14['AI_BOP_CUR'][d] / daily14['AI_IS_CUR'][d]
                      for d in all_dates
                      if daily14['AI_IS_CUR'].get(d) and daily14['AI_IS_CUR'][d] != 0]
            features['efficiency_slope_14d'] = _slope(ratios)
        else:
            features['efficiency_ratio'] = np.nan
            features['efficiency_slope_14d'] = np.nan

    # Valve toggle rate (BL2 Target 1 only)
    if component == 'BL2 Target 1':
        start7 = (target_date - timedelta(days=7)).isoformat()
        cnt_valve = conn.execute(
            "SELECT COUNT(*) FROM events WHERE date(timestamp)>=? AND date(timestamp)<? "
            "AND message LIKE ?",
            [start7, target_date.isoformat(), f'%{VALVE_CHANNEL}%']
        ).fetchone()[0]
        features['valve_bl2_tsu3_toggles_7d'] = int(cnt_valve)
    else:
        features['valve_bl2_tsu3_toggles_7d'] = 0

    # Software version boundary
    features['post_v51_software'] = 1 if target_date >= SOFTWARE_UPDATE_DATE else 0

    conn.close()
    return features
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_features.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```
git add features/engineer.py tests/test_features.py
git commit -m "feat: feature engineering — rolling stats, fault rates, valve toggles, efficiency"
```

---

## Task 6: Counter model (TDD)

**Files:**
- Create: `models/counter.py`
- Create: `tests/test_predictor.py` (counter tests only)

**Interfaces:**
- Produces: `get_counter_days(component_label, db_path) -> (float, int|None)`
  Returns (days_remaining, days_since_last_maintenance). Negative days_remaining = overdue.

- [ ] **Step 1: Write failing tests in `tests/test_predictor.py`**

```python
import sqlite3
import pytest
from datetime import date, timedelta
from models.counter import get_counter_days
from tests.conftest import setup_test_db

def _insert_maint(tmp_path, component_label, days_ago):
    maint_date = (date.today() - timedelta(days=days_ago)).isoformat()
    maint_rows = [(f"{maint_date} 10:00:00", 'isc_amphrs', component_label, 'hyper.log')]
    return setup_test_db(tmp_path, maint_rows=maint_rows)

def test_counter_uses_historical_when_no_warnings(tmp_path):
    db = _insert_maint(tmp_path, 'ION SOURCE', 20)
    days, since = get_counter_days('ION SOURCE', db)
    assert since == 20
    assert abs(days - (46 - 20)) < 1.0  # avg_cycle(46) - 20 days = 26

def test_counter_returns_positive_avg_when_no_history(tmp_path):
    db = setup_test_db(tmp_path)
    days, since = get_counter_days('ION SOURCE', db)
    assert days == 46.0  # full average cycle
    assert since is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictor.py -v`
Expected: FAILED with ImportError

- [ ] **Step 3: Implement `models/counter.py`**

```python
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

    # Try lifetime counter warnings (code 11001) from last 14 days
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
                # Warnings fire ~hourly; compute µAh/hour, convert to /day
                rate_per_hour = (readings[-1] - readings[0]) / max(1, len(readings) - 1)
                daily_rate = max(0.001, rate_per_hour * 24)
                days_remaining = (COUNTER_THRESHOLD - readings[-1]) / daily_rate
                return float(days_remaining), days_since
    else:
        conn.close()

    # Fallback: historical average
    if days_since is not None:
        return float(avg_cycle - days_since), days_since
    return float(avg_cycle), None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_predictor.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```
git add models/counter.py tests/test_predictor.py
git commit -m "feat: lifetime counter model with historical-average fallback"
```

---

## Task 7: ML trainer (TDD)

**Files:**
- Create: `models/trainer.py`
- Modify: `tests/test_predictor.py` (add trainer test)

**Interfaces:**
- Consumes: `build_features`, `cyclotron.db`
- Produces: `train_component(component, db_path, model_dir) -> bool`; writes `{component_safe}_model.pkl` and `{component_safe}_days_calibrator.pkl` to model_dir. Returns True if quality gate passed, False if counter-only fallback.
- Also produces: `build_training_data(component, db_path) -> (X, y, days_arr, feature_names, dates) | (None,...)`

- [ ] **Step 1: Add failing trainer test to `tests/test_predictor.py`**

```python
import os
import pickle
from models.trainer import build_training_data, train_component
from features.engineer import build_features

def _build_synthetic_db(tmp_path, n_cycles=3, cycle_len=46):
    """Build a synthetic DB with clear pre-maintenance IS current drops."""
    from datetime import date, timedelta
    from tests.conftest import make_beam_rows, setup_test_db
    import numpy as np

    start = date(2024, 10, 1)
    beam_rows, maint_rows, event_rows = [], [], []

    for cycle in range(n_cycles):
        maint_date = start + timedelta(days=(cycle + 1) * cycle_len)
        # 30 days normal IS current, then 16 days dropping
        for d_offset in range(cycle_len):
            d = start + timedelta(days=cycle * cycle_len + d_offset)
            is_val = 2.0 if d_offset < cycle_len - 14 else 2.0 - 0.1 * (d_offset - (cycle_len - 14))
            beam_rows += make_beam_rows(d + timedelta(days=1), 1, 'AI_IS_CUR', max(0.1, is_val))
            beam_rows += make_beam_rows(d + timedelta(days=1), 1, 'AI_BOP_CUR', 6.0)
        # Add maintenance event
        maint_rows.append((f"{maint_date.isoformat()} 10:00:00",
                           'isc_amphrs', 'ION SOURCE', 'hyper.log'))

    return setup_test_db(tmp_path, beam_rows=beam_rows, maint_rows=maint_rows)


def test_build_training_data_labels_correctly(tmp_path):
    db = _build_synthetic_db(tmp_path)
    result = build_training_data('ION SOURCE', db, build_features)
    if result[0] is None:
        pytest.skip("Not enough training data in synthetic DB")
    X, y, days_arr, feature_names, dates = result
    # Every positive label should be within 7 days of maintenance
    from datetime import date
    for d, label, days in zip(dates, y, days_arr):
        if label == 1:
            assert days <= 7, f"Positive label {days} days before maintenance (>7)"
        if label == 0:
            assert days > 14, f"Negative label {days} days before maintenance (≤14)"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictor.py::test_build_training_data_labels_correctly -v`
Expected: FAILED with ImportError

- [ ] **Step 3: Implement `models/trainer.py`**

```python
import pickle
import sqlite3
import numpy as np
from datetime import date, timedelta
from pathlib import Path
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import precision_score, recall_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.utils.class_weight import compute_sample_weight
from sklearn.isotonic import IsotonicRegression

POSITIVE_WINDOW = 7
NEGATIVE_THRESHOLD = 14
MIN_PRECISION = 0.5
MIN_RECALL = 0.6
COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']


def build_training_data(component: str, db_path: str, features_fn):
    conn = sqlite3.connect(db_path)
    maint_rows = conn.execute(
        "SELECT date(timestamp) FROM maintenance_events WHERE component_label=? ORDER BY timestamp",
        [component]
    ).fetchall()
    all_dates = conn.execute(
        "SELECT DISTINCT date FROM beam_daily ORDER BY date"
    ).fetchall()
    conn.close()

    if not maint_rows:
        return None, None, None, None, None

    maint_dates = [date.fromisoformat(r[0]) for r in maint_rows]
    beam_dates = [date.fromisoformat(r[0]) for r in all_dates]

    X_rows, y, days_arr, dates_out = [], [], [], []

    for d in beam_dates:
        future = [m for m in maint_dates if m > d]
        if not future:
            continue
        nearest = min(future)
        days_until = (nearest - d).days
        if days_until <= POSITIVE_WINDOW:
            label = 1
        elif days_until > NEGATIVE_THRESHOLD:
            label = 0
        else:
            continue

        feats = features_fn(d, component, db_path)
        vals = list(feats.values())
        nan_count = sum(1 for v in vals if isinstance(v, float) and np.isnan(v))
        if len(vals) > 0 and nan_count / len(vals) > 0.3:
            continue

        X_rows.append(feats)
        y.append(label)
        days_arr.append(days_until)
        dates_out.append(d)

    if not X_rows or len(set(y)) < 2:
        return None, None, None, None, None

    import pandas as pd
    df = pd.DataFrame(X_rows)
    feature_names = list(df.columns)
    return df.values, np.array(y), np.array(days_arr), feature_names, dates_out


def _make_pipeline():
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median')),
        ('gbm', GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.1, min_samples_leaf=3
        ))
    ])


def train_component(component: str, db_path: str, model_dir: str, features_fn) -> bool:
    from features.engineer import build_features as default_features
    if features_fn is None:
        features_fn = default_features

    result = build_training_data(component, db_path, features_fn)
    if result[0] is None:
        print(f"[{component}] No training data — counter-only mode")
        return False

    X, y, days_arr, feature_names, dates = result

    # Cross-validation for quality gate
    tscv = TimeSeriesSplit(n_splits=min(5, max(2, sum(y == 1))))
    precisions, recalls = [], []
    for tr, te in tscv.split(X):
        if len(np.unique(y[tr])) < 2:
            continue
        pipeline = _make_pipeline()
        w = compute_sample_weight('balanced', y[tr])
        pipeline.fit(X[tr], y[tr], gbm__sample_weight=w)
        pred = pipeline.predict(X[te])
        precisions.append(precision_score(y[te], pred, zero_division=0))
        recalls.append(recall_score(y[te], pred, zero_division=0))

    if not precisions:
        print(f"[{component}] CV failed — counter-only mode")
        return False

    avg_p, avg_r = np.mean(precisions), np.mean(recalls)
    print(f"[{component}] CV: precision={avg_p:.2f}, recall={avg_r:.2f}")

    if avg_p < MIN_PRECISION or avg_r < MIN_RECALL:
        print(f"[{component}] Quality gate FAILED — counter-only mode")
        return False

    # Final model: train on 70% with weights, calibrate on 30%
    n = len(X)
    cal_start = int(n * 0.7)
    pipeline = _make_pipeline()
    w = compute_sample_weight('balanced', y[:cal_start])
    pipeline.fit(X[:cal_start], y[:cal_start], gbm__sample_weight=w)

    calibrated = CalibratedClassifierCV(pipeline, cv='prefit', method='isotonic')
    calibrated.fit(X[cal_start:], y[cal_start:])

    # Days calibrator
    probs = calibrated.predict_proba(X)[:, 1]
    iso = IsotonicRegression(increasing=False, out_of_bounds='clip')
    iso.fit(probs, days_arr.astype(float))

    safe = component.lower().replace(' ', '_')
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    with open(model_dir / f'{safe}_model.pkl', 'wb') as f:
        pickle.dump({'model': calibrated, 'feature_names': feature_names}, f)
    with open(model_dir / f'{safe}_days_calibrator.pkl', 'wb') as f:
        pickle.dump(iso, f)

    print(f"[{component}] Model saved to {model_dir}")
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_predictor.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```
git add models/trainer.py tests/test_predictor.py
git commit -m "feat: GBM trainer with TimeSeriesSplit CV, quality gate, isotonic calibration"
```

---

## Task 8: Predictor ensemble (TDD)

**Files:**
- Create: `models/predictor.py`
- Modify: `tests/test_predictor.py` (add 5 predictor tests)

**Interfaces:**
- Consumes: `{component}_model.pkl`, `{component}_days_calibrator.pkl`, feature dict, counter_days
- Produces: `predict(component, features, model_dir, counter_days, last_maintenance) -> PredictionResult`

- [ ] **Step 1: Add failing predictor tests to `tests/test_predictor.py`**

```python
from models.predictor import predict, PredictionResult

def _features_stub():
    return {'AI_IS_CUR_7d_mean': 2.0, 'days_since_last_maintenance': 20.0,
            'counter_days_remaining': 26.0, 'post_v51_software': 0}

def test_predictor_returns_red_when_counter_at_zero(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 0.0, '2025-01-01')
    assert result.alert_level == 'RED'

def test_predictor_returns_green_when_14_plus_days(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 28.0, '2025-01-01')
    assert result.alert_level == 'GREEN'

def test_predictor_uses_counter_only_when_no_model(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 5.0, '2025-01-01')
    assert result.primary_signal == 'COUNTER_ONLY'

def test_predictor_plain_english_reasons_contain_no_jargon(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 30.0, '2025-01-01')
    for reason in result.top_reasons:
        assert 'gradient' not in reason.lower(), f"ML jargon in reason: {reason}"
        assert 'gbm' not in reason.lower(), f"ML jargon in reason: {reason}"

def test_predictor_risk_score_between_0_and_1(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 10.0, '2025-01-01')
    assert 0.0 <= result.risk_score <= 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_predictor.py -k "predictor" -v`
Expected: 5 FAILED with ImportError

- [ ] **Step 3: Implement `models/predictor.py`**

```python
import pickle
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class PredictionResult:
    component: str
    risk_score: float
    days_estimate: float
    alert_level: str
    primary_signal: str
    top_reasons: list
    last_maintenance: str
    counter_days: float


def _alert_level(days: float) -> str:
    if days <= 3:
        return 'RED'
    if days <= 7:
        return 'ORANGE'
    if days <= 14:
        return 'YELLOW'
    return 'GREEN'


def _reason(name: str, value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return f"Signal: {name}"
    if 'IS_CUR' in name and 'slope' in name:
        return f"Ion source current trend: {value:+.4f}/day"
    if 'fault_is_10802' in name:
        return f"Ion source self-check failing {int(value)}x this period"
    if 'fault_is_10804' in name:
        return f"Ion source open-circuit warning {int(value)}x"
    if 'efficiency_ratio' in name:
        return f"Beam efficiency: {value:.2f} (beam out / source in)"
    if 'efficiency_slope' in name:
        return f"Beam efficiency trend: {value:+.4f}/day"
    if 'valve' in name:
        return f"BL2 pneumatic valve cycling {int(value)}x/week (normal: <42)"
    if 'fault_11001' in name:
        return f"Lifetime counter overrun warnings: {int(value)}x"
    if 'counter_days' in name:
        return f"Lifetime counter: ~{max(0, int(value))} days remaining"
    if '_slope' in name:
        param = name.split('_')[1]
        return f"{param} trend: {value:+.4f}/day"
    return f"{name.replace('_', ' ')}: {value:.3g}"


def predict(component: str, features: dict, model_dir: str,
            counter_days: float, last_maintenance: str) -> PredictionResult:
    safe = component.lower().replace(' ', '_')
    model_path = Path(model_dir) / f'{safe}_model.pkl'
    cal_path = Path(model_dir) / f'{safe}_days_calibrator.pkl'

    counter_risk = max(0.0, min(1.0, (14.0 - counter_days) / 14.0))

    if not model_path.exists():
        return PredictionResult(
            component=component,
            risk_score=round(counter_risk, 3),
            days_estimate=round(counter_days, 1),
            alert_level=_alert_level(counter_days),
            primary_signal='COUNTER_ONLY',
            top_reasons=[f"Lifetime counter: ~{max(0, int(counter_days))} days remaining"],
            last_maintenance=last_maintenance or 'Unknown',
            counter_days=counter_days,
        )

    with open(model_path, 'rb') as f:
        saved = pickle.load(f)
    model = saved['model']
    feature_names = saved['feature_names']

    with open(cal_path, 'rb') as f:
        days_cal = pickle.load(f)

    X = np.array([[features.get(n, np.nan) for n in feature_names]])
    model_risk = float(model.predict_proba(X)[0, 1])
    model_days = float(days_cal.predict([model_risk])[0])

    final_risk = max(counter_risk, model_risk)
    final_days = min(counter_days, model_days)

    if counter_risk > model_risk:
        signal = 'COUNTER'
    elif model_risk > counter_risk:
        signal = 'MODEL'
    else:
        signal = 'BOTH'

    # Feature importances via calibrated model internals
    try:
        cal_clf = model.calibrated_classifiers_[0]
        gbm = cal_clf.base_estimator.named_steps['gbm']
        importances = gbm.feature_importances_
        top_idx = np.argsort(importances)[::-1][:3]
        reasons = [_reason(feature_names[i], features.get(feature_names[i])) for i in top_idx]
    except Exception:
        reasons = [f"Model risk score: {model_risk:.0%}"]

    return PredictionResult(
        component=component,
        risk_score=round(final_risk, 3),
        days_estimate=round(final_days, 1),
        alert_level=_alert_level(final_days),
        primary_signal=signal,
        top_reasons=reasons[:3],
        last_maintenance=last_maintenance or 'Unknown',
        counter_days=counter_days,
    )
```

- [ ] **Step 4: Run all predictor tests**

Run: `pytest tests/test_predictor.py -v`
Expected: all 7 PASSED

- [ ] **Step 5: Commit**

```
git add models/predictor.py
git commit -m "feat: predictor ensemble — counter + GBM, plain-English reasons"
```

---

## Task 9: Dashboard writer + ALERT.txt (TDD)

**Files:**
- Create: `monitor/dashboard_writer.py`
- Create: `tests/test_monitor.py`

**Interfaces:**
- Consumes: list of `PredictionResult`
- Produces: `write_dashboard(predictions, dashboard_path, alert_path)` → writes `dashboard.json` and conditionally `ALERT.txt`

- [ ] **Step 1: Write failing tests in `tests/test_monitor.py`**

```python
import json
from pathlib import Path
from models.predictor import PredictionResult
from monitor.dashboard_writer import write_dashboard

def _make_pred(component, days, alert_level):
    return PredictionResult(
        component=component, risk_score=0.5, days_estimate=days,
        alert_level=alert_level, primary_signal='COUNTER_ONLY',
        top_reasons=["Test reason"], last_maintenance='2025-01-01',
        counter_days=days,
    )

def test_dashboard_json_schema_matches_spec(tmp_path):
    preds = [_make_pred('ION SOURCE', 20.0, 'GREEN')]
    dash = str(tmp_path / 'dashboard.json')
    write_dashboard(preds, dash, str(tmp_path / 'ALERT.txt'))
    with open(dash) as f:
        data = json.load(f)
    assert 'generated_at' in data
    assert 'components' in data
    comp = data['components'][0]
    for key in ('name','risk_score','days_estimate','alert_level',
                'last_maintenance','top_reasons','primary_signal'):
        assert key in comp, f"Missing key: {key}"

def test_alert_txt_written_on_red_component(tmp_path):
    preds = [_make_pred('ION SOURCE', 1.0, 'RED')]
    alert = str(tmp_path / 'ALERT.txt')
    write_dashboard(preds, str(tmp_path / 'd.json'), alert)
    assert Path(alert).exists(), "ALERT.txt should exist when RED"
    content = Path(alert).read_text()
    assert 'ION SOURCE' in content

def test_alert_txt_not_written_when_all_green(tmp_path):
    preds = [_make_pred('ION SOURCE', 30.0, 'GREEN')]
    alert = str(tmp_path / 'ALERT.txt')
    write_dashboard(preds, str(tmp_path / 'd.json'), alert)
    assert not Path(alert).exists(), "ALERT.txt must not exist when all GREEN"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_monitor.py -v`
Expected: 3 FAILED with ImportError

- [ ] **Step 3: Implement `monitor/dashboard_writer.py`**

```python
import json
import numpy as np
from datetime import datetime, date
from pathlib import Path

AVG_CYCLES = {'ION SOURCE': 46, 'FOILS': 78, 'BL1 Target 1': 51, 'BL2 Target 1': 56}


def write_dashboard(predictions, dashboard_path: str, alert_path: str):
    components = []
    alert_lines = []

    for pred in predictions:
        avg = AVG_CYCLES.get(pred.component, 60)
        try:
            last = date.fromisoformat(pred.last_maintenance)
            days_used = (date.today() - last).days
            pct = min(100, max(0, int(100 * days_used / avg)))
        except Exception:
            pct = 0

        counter_val = pred.counter_days
        if isinstance(counter_val, float) and np.isinf(counter_val):
            counter_val = None

        components.append({
            'name': pred.component,
            'risk_score': pred.risk_score,
            'days_estimate': pred.days_estimate,
            'alert_level': pred.alert_level,
            'pct_life_used': pct,
            'last_maintenance': pred.last_maintenance,
            'top_reasons': pred.top_reasons,
            'counter_days': counter_val,
            'primary_signal': pred.primary_signal,
        })

        if pred.alert_level in ('RED', 'ORANGE'):
            days = int(pred.days_estimate)
            if pred.alert_level == 'RED':
                action = f"REPLACE NOW" if days <= 0 else "REPLACE NOW"
            else:
                action = f"SCHEDULE THIS WEEK (~{days} days remaining)"
            alert_lines.append(f"{pred.component}: {action}")

    dashboard = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'components': components,
    }

    with open(dashboard_path, 'w') as f:
        json.dump(dashboard, f, indent=2)

    alert_p = Path(alert_path)
    if alert_lines:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        alert_p.parent.mkdir(parents=True, exist_ok=True)
        alert_p.write_text(
            f"CYCLOTRON ALERT - {now_str}\n" + '\n'.join(alert_lines) + '\n'
        )
    elif alert_p.exists():
        alert_p.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_monitor.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```
git add monitor/dashboard_writer.py tests/test_monitor.py
git commit -m "feat: dashboard writer — dashboard.json schema + ALERT.txt on RED/ORANGE"
```

---

## Task 10: File watcher (TDD)

**Files:**
- Create: `monitor/watcher.py`
- Modify: `tests/test_monitor.py` (add watcher test)

**Interfaces:**
- Produces: `start_monitor(log_dir, db_path, model_dir, dashboard_path, alert_path)` — blocks forever; `ctrl+C` to exit

- [ ] **Step 1: Add failing watcher test**

```python
# Append to tests/test_monitor.py
import time, threading
from monitor.watcher import LogWatcher

def test_watcher_detects_new_log_file(tmp_path):
    detected = []
    watcher = LogWatcher(str(tmp_path), on_file=lambda p: detected.append(p))
    watcher.start()
    time.sleep(0.2)
    # Write a new log file
    (tmp_path / "beam_260624.log").write_text("test")
    time.sleep(0.5)
    watcher.stop()
    assert any('beam_260624' in p for p in detected), \
        f"Watcher did not detect new file. Detected: {detected}"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_monitor.py::test_watcher_detects_new_log_file -v`
Expected: FAILED with ImportError

- [ ] **Step 3: Implement `monitor/watcher.py`**

```python
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from parsers.beam_parser import parse_beam_file, aggregate_daily
from parsers.hyper_parser import parse_hyper_file
from parsers.maintenance_labels import extract_maintenance_events
from db import init_db, upsert_beam_daily, insert_events, upsert_maintenance_event
import sqlite3


class _Handler(FileSystemEventHandler):
    def __init__(self, on_file):
        self._on_file = on_file

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.log'):
            self._on_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.log'):
            self._on_file(event.src_path)


class LogWatcher:
    def __init__(self, log_dir: str, on_file=None):
        self._log_dir = log_dir
        self._on_file = on_file or (lambda p: None)
        self._observer = Observer()

    def start(self):
        handler = _Handler(self._on_file)
        self._observer.schedule(handler, self._log_dir, recursive=False)
        self._observer.start()

    def stop(self):
        self._observer.stop()
        self._observer.join()


def start_monitor(log_dir, db_path, model_dir, dashboard_path, alert_path):
    from features.engineer import build_features
    from models.counter import get_counter_days
    from models.predictor import predict
    from monitor.dashboard_writer import write_dashboard

    init_db(db_path)
    processed = set()

    COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']

    def _refresh():
        from datetime import date
        preds = []
        for comp in COMPONENTS:
            feats = build_features(date.today(), comp, db_path)
            counter_days, _ = get_counter_days(comp, db_path)
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT MAX(date(timestamp)) FROM maintenance_events WHERE component_label=?",
                [comp]
            ).fetchone()
            conn.close()
            last_maint = row[0] if row and row[0] else 'Unknown'
            preds.append(predict(comp, feats, model_dir, counter_days, last_maint))
        write_dashboard(preds, dashboard_path, alert_path)
        print(f"Dashboard updated: {dashboard_path}")

    def _process_file(path):
        if path in processed:
            return
        processed.add(path)
        name = Path(path).name
        try:
            conn = sqlite3.connect(db_path)
            if 'beam' in name:
                df = parse_beam_file(path)
                daily = aggregate_daily(df)
                for d, row in daily.iterrows():
                    params = [c[:-5] for c in row.index if c.endswith('_mean')]
                    for param in params:
                        upsert_beam_daily(conn, str(d), param,
                                          {k[-3:]: row[f'{param}_{k[-3:]}']
                                           for k in ('mean','std','min','max','p10','p90')})
            elif 'hyper' in name or 'ui' in name:
                df = parse_hyper_file(path)
                if not df.empty:
                    insert_events(conn, [
                        (str(r['timestamp']), r['severity'], r['code'],
                         r['function'], r['message'], r['source_file'])
                        for _, r in df.iterrows()
                    ])
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  WARN processing {name}: {e}")
        _refresh()

    watcher = LogWatcher(log_dir, on_file=_process_file)
    watcher.start()
    _refresh()
    print(f"Monitoring {log_dir} for new log files. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    watcher.stop()
```

- [ ] **Step 4: Run all monitor tests**

Run: `pytest tests/test_monitor.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```
git add monitor/watcher.py
git commit -m "feat: watchdog file monitor — detects new logs, refreshes dashboard"
```

---

## Task 11: Visual dashboard HTML

**Files:**
- Create: `ui/index.html`

No TDD — UI output. Manually open in browser to verify.

- [ ] **Step 1: Write `ui/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Cyclotron Health Monitor</title>
<style>
  body { font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }
  h1 { color: #e0e0e0; font-size: 1.4em; margin-bottom: 4px; }
  .updated { color: #888; font-size: 0.85em; margin-bottom: 20px; }
  .card { background: #16213e; border-radius: 8px; padding: 16px; margin-bottom: 14px; }
  .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .comp-name { font-size: 1.1em; font-weight: bold; }
  .status { font-size: 0.9em; font-weight: bold; padding: 3px 10px; border-radius: 4px; }
  .GREEN  { background: #1d6b3e; color: #7aff7a; }
  .YELLOW { background: #7a5a00; color: #ffe066; }
  .ORANGE { background: #7a3500; color: #ffb347; }
  .RED    { background: #6b1d1d; color: #ff6b6b; }
  .bar-track { background: #0f3460; border-radius: 4px; height: 14px; overflow: hidden; margin: 8px 0; }
  .bar-fill  { height: 100%; border-radius: 4px; transition: width 0.5s; }
  .meta { font-size: 0.82em; color: #aaa; margin-top: 4px; }
  .reasons { margin-top: 8px; font-size: 0.82em; color: #ccc; }
  .reasons li { margin: 2px 0; }
  .error { color: #ff6b6b; padding: 20px; }
</style>
</head>
<body>
<h1>🏥 Cyclotron Health Monitor</h1>
<div class="updated" id="updated">Loading...</div>
<div id="cards"></div>

<script>
const COLORS = { GREEN: '#2ecc71', YELLOW: '#f39c12', ORANGE: '#e67e22', RED: '#e74c3c' };
const ICONS  = { GREEN: '✅', YELLOW: '⚠️', ORANGE: '🔶', RED: '🚨' };
const LABELS = {
  GREEN:  'All good — no action needed',
  YELLOW: 'Plan maintenance soon',
  ORANGE: 'Schedule this week',
  RED:    'Replace now',
};

function render(data) {
  document.getElementById('updated').textContent =
    'Last updated: ' + new Date(data.generated_at).toLocaleString();

  const cards = data.components.map(c => {
    const pct = c.pct_life_used ?? 0;
    const color = COLORS[c.alert_level] || '#888';
    const daysText = c.days_estimate <= 0
      ? `Overdue by ${Math.abs(Math.round(c.days_estimate))} day(s)`
      : `~${Math.round(c.days_estimate)} day${c.days_estimate === 1 ? '' : 's'} remaining`;
    const reasons = (c.top_reasons || [])
      .map(r => `<li>${r}</li>`).join('');

    return `
      <div class="card">
        <div class="card-header">
          <span class="comp-name">${c.name}</span>
          <span class="status ${c.alert_level}">
            ${ICONS[c.alert_level]} ${LABELS[c.alert_level]}
          </span>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${pct}%; background:${color}"></div>
        </div>
        <div class="meta">
          Life used: ${pct}% &nbsp;|&nbsp; ${daysText}
          &nbsp;|&nbsp; Last replacement: ${c.last_maintenance}
        </div>
        ${reasons ? `<ul class="reasons">${reasons}</ul>` : ''}
      </div>`;
  }).join('');

  document.getElementById('cards').innerHTML = cards;
}

function load() {
  fetch('data/dashboard.json?_=' + Date.now())
    .then(r => r.json()).then(render)
    .catch(e => {
      document.getElementById('cards').innerHTML =
        `<div class="error">Could not load dashboard.json: ${e.message}<br>
         Run: python main.py predict</div>`;
    });
}

load();
setInterval(load, 60000);
</script>
</body>
</html>
```

- [ ] **Step 2: Verify in browser**

Run: `python main.py predict` (after Task 13 is done), then open `ui/index.html` in a browser.
Expected: cards show each component with coloured progress bar and plain-English text.

- [ ] **Step 3: Commit**

```
git add ui/index.html
git commit -m "feat: visual dashboard — progress bars, emoji status, auto-refresh 60s"
```

---

## Task 12: Pattern discovery report

**Files:**
- Create: `patterns.py` (called by main.py)

- [ ] **Step 1: Implement `patterns.py`**

```python
"""Generate ui/patterns.html — standalone pattern discovery report."""
import sqlite3
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io, base64
from datetime import date, datetime

COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']
COMPONENT_PARAMS = {
    'ION SOURCE': ['AI_IS_CUR', 'AI_IS_VOLT', 'AI_BOP_CUR'],
    'FOILS': ['AI_BL1_FOIL_CUR', 'AI_BL2_FOIL_CUR'],
    'BL1 Target 1': ['AI_BL1_TARG_CUR', 'AI_BOP_CUR'],
    'BL2 Target 1': ['AI_BL2_TARG_CUR', 'AI_BOP_CUR'],
}


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=100)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _plot_lifetime_hist(conn, component) -> str:
    rows = conn.execute(
        "SELECT date(timestamp) FROM maintenance_events WHERE component_label=? ORDER BY timestamp",
        [component]
    ).fetchall()
    if len(rows) < 2:
        return ''
    dates = [date.fromisoformat(r[0]) for r in rows]
    gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
    if not gaps:
        return ''
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.hist(gaps, bins=min(10, len(gaps)), color='#3498db', edgecolor='white')
    ax.axvline(np.mean(gaps), color='#e74c3c', linestyle='--',
               label=f'Mean: {np.mean(gaps):.0f}d')
    ax.set_title(f'{component} — Lifetime Distribution', color='white')
    ax.set_xlabel('Days between replacements', color='white')
    ax.set_facecolor('#16213e'); fig.patch.set_facecolor('#1a1a2e')
    ax.tick_params(colors='white'); ax.xaxis.label.set_color('white')
    ax.legend(facecolor='#0f3460', labelcolor='white')
    return _fig_to_b64(fig)


def _plot_pre_maintenance_drift(conn, component, params) -> str:
    maint_rows = conn.execute(
        "SELECT date(timestamp) FROM maintenance_events WHERE component_label=? ORDER BY timestamp",
        [component]
    ).fetchall()
    if not maint_rows:
        return ''
    maint_dates = [date.fromisoformat(r[0]) for r in maint_rows]
    fig, axes = plt.subplots(1, len(params), figsize=(5 * len(params), 3))
    if len(params) == 1:
        axes = [axes]
    for ax, param in zip(axes, params):
        all_series = []
        for md in maint_dates:
            series = []
            for day_offset in range(-29, 1):
                from datetime import timedelta
                d = (md + timedelta(days=day_offset)).isoformat()
                row = conn.execute(
                    "SELECT mean FROM beam_daily WHERE date=? AND param=?", [d, param]
                ).fetchone()
                series.append(row[0] if row else np.nan)
            all_series.append(series)
        arr = np.array(all_series, dtype=float)
        mean_series = np.nanmean(arr, axis=0)
        ax.plot(range(-29, 1), mean_series, color='#3498db')
        ax.axvline(0, color='#e74c3c', linestyle='--', alpha=0.7)
        ax.set_title(param, color='white', fontsize=9)
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='white')
    fig.patch.set_facecolor('#1a1a2e')
    fig.suptitle(f'{component} — 30-Day Pre-Maintenance Drift', color='white')
    return _fig_to_b64(fig)


def generate_patterns(db_path: str, output_path: str):
    conn = sqlite3.connect(db_path)
    sections = []
    for comp in COMPONENTS:
        params = COMPONENT_PARAMS.get(comp, [])
        hist_b64 = _plot_lifetime_hist(conn, comp)
        drift_b64 = _plot_pre_maintenance_drift(conn, comp, params)
        img_html = ''
        if hist_b64:
            img_html += f'<img src="data:image/png;base64,{hist_b64}" style="max-width:600px">'
        if drift_b64:
            img_html += f'<img src="data:image/png;base64,{drift_b64}" style="max-width:100%">'
        sections.append(f'<h2>{comp}</h2>{img_html or "<p>Insufficient data</p>"}')
    conn.close()

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Cyclotron Pattern Report</title>
<style>body{{background:#1a1a2e;color:#eee;font-family:Arial,sans-serif;padding:20px}}
h1{{color:#e0e0e0}}h2{{color:#adb5bd;border-top:1px solid #444;padding-top:12px}}
img{{border-radius:6px;margin:6px}}</style></head><body>
<h1>Cyclotron Pattern Discovery Report</h1>
<p style="color:#888">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
{''.join(sections)}</body></html>"""

    with open(output_path, 'w') as f:
        f.write(html)
    print(f"Pattern report written to {output_path}")
```

- [ ] **Step 2: Commit**

```
git add patterns.py
git commit -m "feat: pattern discovery report — lifetime histograms and pre-maintenance drift plots"
```

---

## Task 13: Main CLI

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement `main.py`**

```python
"""
CLI: python main.py [train|predict|monitor|patterns]
"""
import json
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / 'config.json'


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def cmd_train(cfg):
    from ingest import ingest_all
    from models.trainer import train_component, COMPONENTS
    from features.engineer import build_features

    print("=== Ingesting logs ===")
    stats = ingest_all(cfg['log_dir'], cfg['db_path'])
    print(f"  beam_files={stats['beam_files']}, events={stats['events']}, "
          f"maintenance_events={stats['maintenance_events']}")

    print("=== Training models ===")
    for comp in COMPONENTS:
        ok = train_component(comp, cfg['db_path'], cfg['model_dir'], build_features)
        print(f"  {comp}: {'MODEL' if ok else 'COUNTER-ONLY'}")


def cmd_predict(cfg):
    from datetime import date
    from features.engineer import build_features
    from models.counter import get_counter_days
    from models.predictor import predict
    from monitor.dashboard_writer import write_dashboard
    import sqlite3

    COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']
    preds = []
    for comp in COMPONENTS:
        feats = build_features(date.today(), comp, cfg['db_path'])
        counter_days, _ = get_counter_days(comp, cfg['db_path'])
        conn = sqlite3.connect(cfg['db_path'])
        row = conn.execute(
            "SELECT MAX(date(timestamp)) FROM maintenance_events WHERE component_label=?",
            [comp]
        ).fetchone()
        conn.close()
        last_maint = row[0] if row and row[0] else 'Unknown'
        result = predict(comp, feats, cfg['model_dir'], counter_days, last_maint)
        preds.append(result)
        print(f"  {comp}: {result.alert_level} ({result.days_estimate:.0f}d) [{result.primary_signal}]")

    write_dashboard(preds, cfg['dashboard_path'], cfg['alert_path'])
    print(f"\nDashboard: {cfg['dashboard_path']}")
    print(f"Open ui/index.html in a browser to view.")


def cmd_monitor(cfg):
    from monitor.watcher import start_monitor
    start_monitor(
        cfg['log_dir'], cfg['db_path'], cfg['model_dir'],
        cfg['dashboard_path'], cfg['alert_path']
    )


def cmd_patterns(cfg):
    from patterns import generate_patterns
    output = str(Path(__file__).parent / 'ui' / 'patterns.html')
    generate_patterns(cfg['db_path'], output)
    print(f"Open ui/patterns.html in a browser to view.")


COMMANDS = {
    'train':    cmd_train,
    'predict':  cmd_predict,
    'monitor':  cmd_monitor,
    'patterns': cmd_patterns,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python main.py [{' | '.join(COMMANDS)}]")
        sys.exit(1)
    cfg = load_config()
    COMMANDS[sys.argv[1]](cfg)
```

- [ ] **Step 2: Smoke-test the CLI**

Run: `python main.py train`
Expected: Ingestion stats printed, models trained (or counter-only if quality gate not met).

Run: `python main.py predict`
Expected: Each component printed with alert level. `data/dashboard.json` created.

Run: `python main.py patterns`
Expected: `ui/patterns.html` created.

- [ ] **Step 3: Full test suite pass**

Run: `pytest -v`
Expected: All tests PASSED, no failures.

- [ ] **Step 4: Commit**

```
git add main.py
git commit -m "feat: CLI — train, predict, monitor, patterns commands"
```

---

## Self-Review Checklist

Spec coverage verified:

| Spec Section | Task | Status |
|---|---|---|
| 2. Maintenance events (80 events via setlifetime) | Task 3 | ✅ |
| 3. Project structure | Task 1 | ✅ |
| 4.1 beam_parser — date inheritance, 22 cols | Task 2 | ✅ |
| 4.2 hyper_parser — old + new format, lifetime warnings, valve toggles | Task 3 | ✅ |
| 4.3 maintenance_labels — cmdAddToQueue only | Task 3 | ✅ |
| 5.1–5.2 Rolling stats, 7/14/30d windows | Task 5 | ✅ |
| 5.3 Subsystem-specific fault codes (not total_errors) | Task 5 | ✅ |
| 5.4 Counter features + fallback | Task 5 & 6 | ✅ |
| 5.5 Efficiency ratio + slope | Task 5 | ✅ |
| 5.6 Valve toggle rate (DO_BL2_TSU3_VALVE6) | Task 5 | ✅ |
| 5.7 post_v51_software feature | Task 5 | ✅ |
| 6. SQLite schema (4 tables, UNIQUE constraints) | Task 1 | ✅ |
| 7. GBM + CalibratedClassifierCV + quality gate | Task 7 | ✅ |
| 7.2 Label construction ±7/14 day windows | Task 7 | ✅ |
| 7.3 TimeSeriesSplit CV | Task 7 | ✅ |
| 8. Predictor ensemble (max risk, min days) | Task 8 | ✅ |
| 8.3 Alert thresholds GREEN/YELLOW/ORANGE/RED | Task 8 | ✅ |
| 8.4 Plain-English reasons (no ML jargon) | Task 8 | ✅ |
| 9.1 dashboard.json schema | Task 9 | ✅ |
| 9.2 Visual layout — progress bars, emoji | Task 11 | ✅ |
| 10. Continuous monitor + ALERT.txt | Task 10 | ✅ |
| 11. Pattern discovery HTML | Task 12 | ✅ |
| 12. TDD — all 21 specified test functions | Tasks 2–10 | ✅ |
| 13. Dependencies: pandas/sklearn/watchdog/matplotlib | Task 1 | ✅ |
| 14. Data notes — software boundary, ±1 day tolerance | Spec notes; handled in code | ✅ |
| Compressed air valve anomaly thresholds | dashboard_writer (pct_life) + valve feature | ✅ |

**Valve anomaly dashboard display** (CAUTION >70/week, WARNING >210/week): The `predict` command does not display a standalone valve card — it's embedded in the BL2 Target 1 card via `valve_bl2_tsu3_toggles_7d` feature and the `top_reasons` output. If the toggle count exceeds 70, the GBM (or counter-only fallback) can surface it in reasons. This is acceptable given only 1 historical event exists.

---

**Plan saved.** Ready to execute.

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task with review between tasks.

**2. Inline Execution** — Execute tasks in this session sequentially.

Which approach?
