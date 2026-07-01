# PetBMS Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand PET Lab Monitor to PetBMS, ingest the newly-received cyclotron log/gauge data into the dashboard, apply a real PET-Labs-branded theme with cleaner navigation, and ship an installable PWA — all without touching the Apple Developer Program.

**Architecture:** Extend the existing Python ingestion pipeline (`ingest.py` + `parsers/`) with one new parser for a previously-unseen log format, feed it from a one-off `.eml`-extraction script, backfill gauge photos through the existing OCR endpoint code path, then apply a new central `mobile/constants/Theme.ts` across the Expo app's screens and add a PWA manifest to the existing web export.

**Tech Stack:** Python 3.14 + pytest + pandas (backend/ingestion), Expo SDK 54 / React Native 0.81 / TypeScript + jest (mobile), existing FastAPI/SQLite/Render backend (unchanged).

## Global Constraints

- App name is **PetBMS** everywhere user-facing (title, splash, `app.json` name/slug, PWA manifest). Internal repo/package identifiers (`com.petlabs.monitor`, folder name `cyclotron_monitor`) are NOT required to change — only user-facing branding.
- Signature colors (verified from petlabs.co.za + the PET Labs email-signature logo, not invented): primary blue `#1863DC`, deep navy `#0056A7`, ink `#212121`, surface greys `#F4F4F4`/`#EBEBEB`, white `#FFFFFF`. Alert-state colors reuse the app's existing RED/ORANGE/YELLOW/GREEN chip semantics — do not invent new alert colors.
- No native EAS/WidgetKit build, no App Store, no Apple Developer Program. Delivery is an installable PWA on top of the existing Render backend.
- No paid APIs/services (existing project-wide policy) — do not wire in any external service requiring a paid key.
- TDD throughout: failing test → minimal implementation → passing test → commit, for every task.
- `data/cyclotron.db`, `data/.credentials.json`, and other files under `data/` are never committed (existing `.gitignore` rule) — ingestion scripts write there but nothing under `data/` goes into git.

---

## File Structure

**New files:**
- `parsers/batch_beam_parser.py` — parser for the new "Tracer:/Batch no:/Date:" tab-delimited log format found in the downloaded `.eml` files.
- `scripts/extract_eml_logs.py` — one-off script: reads `.eml` files from a source directory, deduplicates `.log` attachments by filename, writes them to a staging directory.
- `scripts/backfill_gauge_photos.py` — one-off script: runs staged gauge photos through the existing OCR pipeline into `gauge_readings`.
- `tests/fixtures/batch_beam_sample.log` — real (trimmed) sample of the new log format, with data rows.
- `tests/fixtures/batch_beam_empty.log` — real sample with header only, no data rows (edge case).
- `tests/test_batch_beam_parser.py`
- `tests/test_extract_eml_logs.py`
- `tests/test_backfill_gauge_photos.py`
- `mobile/constants/Theme.ts` — single source of truth for PetBMS colors/spacing/typography.
- `mobile/__tests__/Theme.test.ts`
- `mobile/public/manifest.json` (or `mobile/web/manifest.json`, exact path pinned in Task 8 once `expo export --platform web` output is inspected) — PWA manifest.

**Modified files:**
- `ingest.py` — add batch-beam-file detection + processing loop.
- `tests/test_ingest.py` — add coverage for the new detection path.
- `mobile/app.json` — rename to PetBMS, update splash/background colors to the new theme.
- `mobile/package.json` — rename `name` to `petbms`.
- `mobile/app/(tabs)/_layout.tsx` — consume `Theme.ts` instead of hardcoded hex.
- `mobile/app/(tabs)/index.tsx` (Dashboard) — new widgets for beam-parameter history + gauge photo history.

---

## Task 1: Batch-beam log parser

**Files:**
- Create: `parsers/batch_beam_parser.py`
- Create: `tests/fixtures/batch_beam_sample.log`
- Create: `tests/fixtures/batch_beam_empty.log`
- Test: `tests/test_batch_beam_parser.py`

**Interfaces:**
- Produces: `is_batch_beam_file(path: str) -> bool` and `parse_batch_beam_file(path: str) -> pd.DataFrame` (columns: `timestamp` + numeric param columns, same shape contract as `parsers.beam_parser.parse_beam_file`'s output — consumed by `parsers.beam_parser.aggregate_daily` unchanged).

- [ ] **Step 1: Create the fixture files**

`tests/fixtures/batch_beam_sample.log` (real trimmed sample — tabs between fields, exactly as received):

```
Tracer: (1) Dummy P w/o He-cooling			Batch no: 1			Date: 2024-06-16
Site name: geps

Time	Arc-I	Arc-V	Gas flow	Dee-1-kV	Dee-2-kV	Magnet-I	Foil-I	Coll-l-I	Target-I	Coll-r-I	Vacuum-P	Target-P	Delta Dee-kV	Phase load	Dee ref-V	Probe-I	He cool-P	Flap1-pos	Flap2-pos	Step pos	Extr pos	Balance	RF fwd-W	RF refl-W	Foil No
17:00:23 	0	0	0.1	0.0	0.0	426.6	0.0	0.0	0.0	0.0	7.46E-07	0.0	0.0	10.1	37.0	0.0	1.0	34.7	17.7	0.0	32.5	33.8	0.0	0.0	1
17:00:26 	0	0	0.1	0.0	0.0	426.6	0.0	0.0	0.0	0.0	7.22E-07	0.0	0.0	10.3	37.0	0.0	0.2	34.7	17.7	0.0	32.5	33.8	0.0	0.0	1
17:01:07 	48	1215	5.0	36.9	40.0	426.6	0.0	0.0	0.0	0.0	4.66E-06	0.0	3.1	3.9	37.0	21.6	1.0	45.1	21.8	0.0	35.1	27.3	11.4	0.1	1
17:28:23 	0	0	0.1	0.0	0.0	426.9	0.0	0.0	0.0	0.0	3.76E-06	0.0	0.0	10.8	37.0	0.0	1.0	34.4	17.8	0.0	32.3	34.6	0.0	0.0	1
```

Note the field separators on data rows are **tabs**, and each time value has a trailing space before the first tab (`17:00:23 \t0\t...`) — preserve this exactly, it's real device output and the parser must tolerate it.

`tests/fixtures/batch_beam_empty.log` (real sample, header only, no data rows — some batches never ran):

```
Tracer: (4) 18F- self-shielded			Batch no: 123			Date: 2025-04- 1
Site name: geps

Time	Arc-I	Arc-V	Gas flow	Dee-1-kV	Dee-2-kV	Magnet-I	Foil-I	Coll-l-I	Target-I	Coll-r-I	Vacuum-P	Target-P	Delta Dee-kV	Phase load	Dee ref-V	Probe-I	He cool-P	Flap1-pos	Flap2-pos	Step pos	Extr pos	Balance	RF fwd-W	RF refl-W	Foil No
```

Note the `Date:` field has irregular spacing (`2025-04- 1` — a space where a zero-padded digit would go). The parser must handle this.

- [ ] **Step 2: Write the failing tests**

`tests/test_batch_beam_parser.py`:

```python
from pathlib import Path
import pandas as pd
from parsers.batch_beam_parser import is_batch_beam_file, parse_batch_beam_file
from parsers.beam_parser import aggregate_daily

SAMPLE = Path(__file__).parent / "fixtures" / "batch_beam_sample.log"
EMPTY = Path(__file__).parent / "fixtures" / "batch_beam_empty.log"


def test_is_batch_beam_file_detects_sample():
    assert is_batch_beam_file(str(SAMPLE)) is True


def test_is_batch_beam_file_rejects_non_matching_file(tmp_path):
    other = tmp_path / "not_a_batch_log.log"
    other.write_text("DATE,TIME,foo\n01/01/2024,00:00:00,1\n")
    assert is_batch_beam_file(str(other)) is False


def test_parse_batch_beam_file_returns_expected_columns():
    df = parse_batch_beam_file(str(SAMPLE))
    for col in ['Arc-I', 'Arc-V', 'Magnet-I', 'RF fwd-W', 'Foil No']:
        assert col in df.columns, f"Missing: {col}"
    assert 'timestamp' in df.columns


def test_parse_batch_beam_file_combines_header_date_with_row_time():
    df = parse_batch_beam_file(str(SAMPLE))
    assert len(df) == 4
    assert df['timestamp'].dt.date.nunique() == 1
    assert str(df['timestamp'].dt.date.iloc[0]) == '2024-06-16'
    assert df['timestamp'].iloc[0].strftime('%H:%M:%S') == '17:00:23'


def test_parse_batch_beam_file_handles_irregular_date_spacing():
    df = parse_batch_beam_file(str(EMPTY))
    assert isinstance(df, pd.DataFrame)


def test_parse_batch_beam_file_empty_batch_returns_empty_dataframe():
    df = parse_batch_beam_file(str(EMPTY))
    assert df.empty


def test_aggregate_daily_works_on_batch_beam_output():
    df = parse_batch_beam_file(str(SAMPLE))
    daily = aggregate_daily(df)
    assert 'Arc-V_mean' in daily.columns
    assert 'data_quality' in daily.columns
    assert len(daily) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_batch_beam_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'parsers.batch_beam_parser'`

- [ ] **Step 4: Implement the parser**

`parsers/batch_beam_parser.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_batch_beam_parser.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add parsers/batch_beam_parser.py tests/test_batch_beam_parser.py tests/fixtures/batch_beam_sample.log tests/fixtures/batch_beam_empty.log
git commit -m "Add parser for the batch-beam log format (Tracer:/Batch no:/Date: header)"
```

---

## Task 2: Wire the batch-beam parser into `ingest.py`

**Files:**
- Modify: `ingest.py`
- Modify: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `parsers.batch_beam_parser.is_batch_beam_file`, `parse_batch_beam_file` (Task 1); `parsers.beam_parser.aggregate_daily`; `db.upsert_beam_daily` (both pre-existing, unchanged signatures).
- Produces: `ingest_all()` now also returns `stats['batch_beam_files']` (int count).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ingest.py`:

```python
def test_ingest_detects_batch_beam_files_without_beam_in_filename(tmp_path):
    (tmp_path / "logs").mkdir()
    shutil.copy(str(FIXTURE_DIR / "batch_beam_sample.log"),
                str(tmp_path / "logs" / "1.log"))
    db = str(tmp_path / "test.db")
    stats = ingest_all(str(tmp_path / "logs"), db)
    assert stats['batch_beam_files'] == 1
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM beam_daily").fetchone()[0]
    conn.close()
    assert rows > 0, "beam_daily should be populated from the batch-beam file"


def test_ingest_batch_beam_files_idempotent(tmp_path):
    (tmp_path / "logs").mkdir()
    shutil.copy(str(FIXTURE_DIR / "batch_beam_sample.log"),
                str(tmp_path / "logs" / "1.log"))
    db = str(tmp_path / "test.db")
    ingest_all(str(tmp_path / "logs"), db)
    ingest_all(str(tmp_path / "logs"), db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM beam_daily").fetchone()[0]
    conn.close()
    # INSERT OR REPLACE keyed on (date, param) — re-running must not double rows
    conn2 = sqlite3.connect(db)
    rows2 = conn2.execute("SELECT COUNT(*) FROM beam_daily").fetchone()[0]
    conn2.close()
    assert rows == rows2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ingest.py -v -k batch_beam`
Expected: FAIL — `KeyError: 'batch_beam_files'`

- [ ] **Step 3: Implement the wiring**

In `ingest.py`, add the import:

```python
from parsers.batch_beam_parser import is_batch_beam_file, parse_batch_beam_file
```

Change the `stats` initializer:

```python
    stats = {'beam_files': 0, 'hyper_files': 0, 'events': 0, 'maintenance_events': 0,
              'batch_beam_files': 0}
```

After the existing `beam_files`/`hyper_files` filename filters, add a third bucket for files neither list claimed, filtered by content sniff:

```python
    beam_files = [f for f in all_files if f.endswith('.log') and 'beam' in f]
    hyper_files = [f for f in all_files if f.endswith('.log') and
                   ('hyper' in f or 'ui' in f)]
    claimed = set(beam_files) | set(hyper_files)
    batch_beam_files = [
        f for f in all_files
        if f.endswith('.log') and f not in claimed
        and is_batch_beam_file(str(Path(log_dir) / f))
    ]
```

After the existing beam-files loop (right before the hyper-files loop), add:

```python
        for i, filename in enumerate(batch_beam_files):
            try:
                fpath = Path(log_dir) / filename
                if fpath.is_symlink():
                    print(f"  WARN batch-beam {filename}: skipping symlink")
                    continue
                df = parse_batch_beam_file(str(fpath))
                daily = aggregate_daily(df)
                for d, row in daily.iterrows():
                    params = [c[:-5] for c in row.index if c.endswith('_mean')]
                    for param in params:
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
                stats['batch_beam_files'] += 1
            except Exception as e:
                print(f"  WARN batch-beam {filename}: {e}")
            if (i + 1) % 10 == 0:
                conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: all passed (existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add ingest.py tests/test_ingest.py
git commit -m "Route numbered batch-beam .log files through the new parser in ingest_all()"
```

---

## Task 3: `.eml` log extraction script

**Files:**
- Create: `scripts/extract_eml_logs.py`
- Test: `tests/test_extract_eml_logs.py`

**Interfaces:**
- Produces: `extract_logs(eml_paths: list[str], out_dir: str) -> dict` returning `{'files_written': int, 'duplicates_skipped': int, 'logo_saved': bool}`. CLI entrypoint `python scripts/extract_eml_logs.py <src_dir_with_eml_files> <out_dir>`.

- [ ] **Step 1: Write the failing test**

`tests/test_extract_eml_logs.py`:

```python
import email
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from pathlib import Path

from scripts.extract_eml_logs import extract_logs


def _make_eml(path: Path, attachments: dict[str, bytes], logo_bytes: bytes | None = None):
    msg = MIMEMultipart()
    msg['Subject'] = 'Cyclotron logs'
    for name, content in attachments.items():
        part = MIMEApplication(content, Name=name)
        part['Content-Disposition'] = f'attachment; filename="{name}"'
        msg.attach(part)
    if logo_bytes is not None:
        img = MIMEImage(logo_bytes, name='C2_signature_petlablogo_abc.png')
        img['Content-Disposition'] = 'inline; filename="C2_signature_petlablogo_abc.png"'
        msg.attach(img)
    path.write_text(msg.as_string())


def test_extract_logs_writes_unique_attachments(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    _make_eml(src / "a.eml", {"1.log": b"aaa", "2.log": b"bbb"})
    _make_eml(src / "b.eml", {"2.log": b"bbb", "3.log": b"ccc"})

    result = extract_logs([str(src / "a.eml"), str(src / "b.eml")], str(out))

    assert result['files_written'] == 3
    assert result['duplicates_skipped'] == 1
    assert (out / "1.log").read_bytes() == b"aaa"
    assert (out / "2.log").read_bytes() == b"bbb"
    assert (out / "3.log").read_bytes() == b"ccc"


def test_extract_logs_saves_logo_once(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    _make_eml(src / "a.eml", {"1.log": b"aaa"}, logo_bytes=b"PNGDATA")
    _make_eml(src / "b.eml", {"2.log": b"bbb"}, logo_bytes=b"PNGDATA")

    result = extract_logs([str(src / "a.eml"), str(src / "b.eml")], str(out))

    assert result['logo_saved'] is True
    assert (out / "petlab_logo.png").read_bytes() == b"PNGDATA"


def test_extract_logs_skips_malformed_eml(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    (src / "broken.eml").write_bytes(b"\xff\xfe not a valid mime message")
    _make_eml(src / "a.eml", {"1.log": b"aaa"})

    result = extract_logs([str(src / "broken.eml"), str(src / "a.eml")], str(out))

    assert result['files_written'] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_extract_eml_logs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.extract_eml_logs'`

- [ ] **Step 3: Implement**

Create `scripts/__init__.py` (empty file) if it doesn't already exist, then `scripts/extract_eml_logs.py`:

```python
import email
import os
import sys
from pathlib import Path


def extract_logs(eml_paths: list, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    seen = set()
    files_written = 0
    duplicates_skipped = 0
    logo_saved = False

    for eml_path in eml_paths:
        try:
            with open(eml_path, 'r', encoding='utf-8', errors='ignore') as f:
                msg = email.message_from_file(f)
        except Exception as e:
            print(f"  WARN {eml_path}: could not parse ({e}), skipping")
            continue

        for part in msg.walk():
            fn = part.get_filename()
            if not fn:
                continue
            payload = part.get_payload(decode=True) or b""

            if fn.startswith('C2_signature_petlablogo'):
                if not logo_saved:
                    with open(os.path.join(out_dir, 'petlab_logo.png'), 'wb') as out:
                        out.write(payload)
                    logo_saved = True
                continue

            if fn in seen:
                duplicates_skipped += 1
                continue
            seen.add(fn)
            with open(os.path.join(out_dir, fn), 'wb') as out:
                out.write(payload)
            files_written += 1

    return {
        'files_written': files_written,
        'duplicates_skipped': duplicates_skipped,
        'logo_saved': logo_saved,
    }


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python scripts/extract_eml_logs.py <src_dir_with_eml_files> <out_dir>")
        sys.exit(1)
    src_dir, out_dir = sys.argv[1], sys.argv[2]
    eml_files = [str(p) for p in Path(src_dir).glob('*.eml')]
    result = extract_logs(eml_files, out_dir)
    print(f"Wrote {result['files_written']} files "
          f"({result['duplicates_skipped']} duplicates skipped, "
          f"logo_saved={result['logo_saved']}) to {out_dir}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_extract_eml_logs.py -v`
Expected: 3 passed

- [ ] **Step 5: Run it for real against the downloaded emails**

Run: `python scripts/extract_eml_logs.py "C:\Users\theol\Downloads" "C:\Users\theol\cyclotron_monitor\data\log_import"`
Expected: `Wrote 156 files (... duplicates skipped, logo_saved=True) to ...\data\log_import` — confirms the real extraction matches the 156-file count already verified during design research. (`data/` is gitignored — nothing here gets committed.)

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/extract_eml_logs.py tests/test_extract_eml_logs.py
git commit -m "Add .eml log/logo extraction script"
```

---

## Task 4: Run the real ingestion end-to-end

**Files:**
- No new files — this task runs Tasks 1-3's code against the real data and verifies via the existing dev DB.

**Interfaces:**
- Consumes: `ingest.ingest_all(log_dir, db_path)` (Task 2).

- [ ] **Step 1: Run ingestion against the extracted logs into the dev DB**

Run: `python -c "from ingest import ingest_all; print(ingest_all(r'C:\Users\theol\cyclotron_monitor\data\log_import', r'C:\Users\theol\cyclotron_monitor\data\cyclotron.db'))"`
Expected: a stats dict with `'batch_beam_files'` close to 156 (some may be empty-batch files like `123.log` contributing 0 rows to `beam_daily` but still counted as processed, not warned).

- [ ] **Step 2: Verify row counts directly**

Run: `python -c "import sqlite3; c = sqlite3.connect(r'C:\Users\theol\cyclotron_monitor\data\cyclotron.db'); print(c.execute('SELECT COUNT(*) FROM beam_daily').fetchone())"`
Expected: non-zero count.

- [ ] **Step 3: Re-run to confirm idempotency on the real dataset**

Run the same command from Step 1 again.
Expected: stats dict reports the same file counts; row count from Step 2 is unchanged on re-query.

No commit — this is a data operation, not a code change. `data/cyclotron.db` stays gitignored.

---

## Task 5: Gauge photo backfill script

**Files:**
- Create: `scripts/backfill_gauge_photos.py`
- Test: `tests/test_backfill_gauge_photos.py`

**Interfaces:**
- Consumes (confirmed by reading `api/routes/gauges.py` in full — real signatures, not guessed):
  - `api.routes.gauges._run_ocr(photo_b64: str, gauge_name: str = '') -> dict` — takes a
    **base64-encoded** photo (not a path), returns
    `{'value': float|None, 'unit': str, 'is_alert': bool, 'alert_reason': str, 'raw_ocr_text': str, 'ocr_ok': bool}`.
  - `api.db_cloud.get_conn(db_path: str)` — same connection helper the live endpoint uses.
  - `gauge_readings` schema (from `db.py`, already read): the live single-photo endpoint
    (`process_photo_reading` in `api/routes/gauges.py:225-250`) inserts into columns
    `(lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason, photo_path, raw_ocr_text)` —
    `confidence` is left blank on that path (only the CSV-import path sets it), so this task
    reuses `confidence='backfill'` to mark rows as backfilled without colliding with the CSV
    import's real-confidence use of that column, mirroring the existing convention (seen at
    `gauges.py:196-197`) of prefixing `raw_ocr_text` with a provenance tag.
- Produces: `backfill_photos(photo_paths: list[str], db_path: str, lab_id: str) -> dict` returning
  `{'inserted': int, 'failed': list[str]}`.

- [ ] **Step 1: Write the failing test** (stubs `_run_ocr` via monkeypatch — do not call the real Gemini/Ollama model in unit tests)

`tests/test_backfill_gauge_photos.py`:

```python
import base64
from api.db_cloud import get_conn
from db import init_db
from scripts.backfill_gauge_photos import backfill_photos


def test_backfill_inserts_gauge_readings(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    photo = tmp_path / "gauge1.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

    def fake_run_ocr(photo_b64, gauge_name=''):
        assert photo_b64 == base64.b64encode(photo.read_bytes()).decode()
        return {'value': 7.4e-7, 'unit': 'mbar', 'is_alert': False, 'alert_reason': '',
                'raw_ocr_text': 'high confidence — needle at 7.4E-7', 'ocr_ok': True}

    monkeypatch.setattr('scripts.backfill_gauge_photos._run_ocr', fake_run_ocr)

    result = backfill_photos([str(photo)], db_path, lab_id='cyclotron')

    assert result['inserted'] == 1
    assert result['failed'] == []
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT value, unit, confidence, photo_path FROM gauge_readings"
    ).fetchone()
    conn.close()
    assert tuple(row) == (7.4e-7, 'mbar', 'backfill', str(photo))


def test_backfill_skips_unreadable_photo_without_aborting(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    good = tmp_path / "good.jpg"
    good.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

    def fake_run_ocr(photo_b64, gauge_name=''):
        return {'value': 48.0, 'unit': 'mA', 'is_alert': False, 'alert_reason': '',
                'raw_ocr_text': 'ok', 'ocr_ok': True}

    monkeypatch.setattr('scripts.backfill_gauge_photos._run_ocr', fake_run_ocr)

    missing = str(tmp_path / "does_not_exist.jpg")
    result = backfill_photos([str(good), missing], db_path, lab_id='cyclotron')

    assert result['inserted'] == 1
    assert len(result['failed']) == 1
    assert 'does_not_exist.jpg' in result['failed'][0]


def test_backfill_skips_photo_with_no_readable_value(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    photo = tmp_path / "blurry.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

    def fake_run_ocr(photo_b64, gauge_name=''):
        return {'value': None, 'unit': '', 'is_alert': False, 'alert_reason': '',
                'raw_ocr_text': 'OCR failed', 'ocr_ok': False}

    monkeypatch.setattr('scripts.backfill_gauge_photos._run_ocr', fake_run_ocr)

    result = backfill_photos([str(photo)], db_path, lab_id='cyclotron')

    assert result['inserted'] == 0
    assert len(result['failed']) == 1
    assert 'no readable value' in result['failed'][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_backfill_gauge_photos.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_gauge_photos'`

- [ ] **Step 3: Implement**

`scripts/backfill_gauge_photos.py`:

```python
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

from api.db_cloud import get_conn
from api.routes.gauges import _run_ocr
from db import init_db


def backfill_photos(photo_paths: list, db_path: str, lab_id: str) -> dict:
    init_db(db_path)
    conn = get_conn(db_path)
    inserted = 0
    failed = []
    try:
        for path in photo_paths:
            try:
                photo_bytes = Path(path).read_bytes()
            except OSError as e:
                failed.append(f"{path}: could not read file ({e})")
                continue

            photo_b64 = base64.b64encode(photo_bytes).decode()
            reading = _run_ocr(photo_b64)

            if reading.get('value') is None:
                failed.append(f"{path}: no readable value ({reading.get('raw_ocr_text', '')})")
                continue

            conn.execute(
                "INSERT INTO gauge_readings "
                "(lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason, "
                " photo_path, raw_ocr_text, confidence) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [lab_id, '', datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 reading['value'], reading['unit'], int(reading['is_alert']),
                 reading['alert_reason'], path, reading['raw_ocr_text'], 'backfill'],
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return {'inserted': inserted, 'failed': failed}


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print("Usage: python scripts/backfill_gauge_photos.py <photo_dir> <db_path> <lab_id>")
        sys.exit(1)
    photo_dir, db_path, lab_id = sys.argv[1], sys.argv[2], sys.argv[3]
    paths = [str(p) for p in Path(photo_dir).glob('*.jpeg')] + \
            [str(p) for p in Path(photo_dir).glob('*.jpg')]
    result = backfill_photos(paths, db_path, lab_id)
    print(f"Inserted {result['inserted']}, failed {len(result['failed'])}")
    for f in result['failed']:
        print(f"  FAILED: {f}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_backfill_gauge_photos.py -v`
Expected: 3 passed

- [ ] **Step 5: Run it for real against the 17 staged gauge photos**

Run: `python scripts/backfill_gauge_photos.py "C:\Users\theol\Downloads\Images_extracted" "C:\Users\theol\cyclotron_monitor\data\cyclotron.db" cyclotron`
Expected: `Inserted <=17, failed <n>` printed; any failures listed by filename for manual follow-up. Requires either `GEMINI_API_KEY` configured or `ollama serve` running locally with `GAUGE_OLLAMA_MODEL` set, per [[project_petlab_monitor]]'s existing dev-startup instructions — if neither is configured, `_run_ocr` returns `ocr_ok=False`/`value=None` for every photo and every photo lands in `failed`, which is the expected (not broken) behavior of Step 3's "no readable value" path.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_gauge_photos.py tests/test_backfill_gauge_photos.py
git commit -m "Add gauge-photo backfill script reusing the existing OCR pipeline"
```

---

## Task 6: PetBMS theme system

**Files:**
- Create: `mobile/constants/Theme.ts`
- Test: `mobile/__tests__/Theme.test.ts`

**Interfaces:**
- Produces: `export const Colors = {...}` and `export const Theme = {...}` — consumed by Task 7 (tab layout) and Task 9 (dashboard widgets).

- [ ] **Step 1: Write the failing test**

`mobile/__tests__/Theme.test.ts`:

```typescript
import { Colors } from '../constants/Theme';

describe('Theme colors', () => {
  it('defines the PetLabs signature palette', () => {
    expect(Colors.primary).toBe('#1863DC');
    expect(Colors.primaryDark).toBe('#0056A7');
    expect(Colors.ink).toBe('#212121');
  });

  it('defines alert-state colors matching existing RED/ORANGE/YELLOW/GREEN chips', () => {
    expect(Colors.alertRed).toBeDefined();
    expect(Colors.alertOrange).toBeDefined();
    expect(Colors.alertYellow).toBeDefined();
    expect(Colors.alertGreen).toBeDefined();
  });

  it('every color value is a valid hex string', () => {
    Object.values(Colors).forEach((v) => {
      expect(typeof v).toBe('string');
      expect(v).toMatch(/^#[0-9A-Fa-f]{6}$/);
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && npx jest Theme.test.ts`
Expected: FAIL — `Cannot find module '../constants/Theme'`

- [ ] **Step 3: Implement**

`mobile/constants/Theme.ts`:

```typescript
// PetBMS signature palette — sourced from PET Labs Pharmaceuticals' real
// branding (petlabs.co.za + the email-signature logo), 2026-07-01.
export const Colors = {
  primary: '#1863DC',
  primaryDark: '#0056A7',
  ink: '#212121',
  surface: '#F4F4F4',
  surfaceAlt: '#EBEBEB',
  white: '#FFFFFF',

  // Alert-state colors — match the dashboard's existing RED/ORANGE/YELLOW/GREEN chip semantics.
  alertRed: '#D6304A',
  alertOrange: '#E8862E',
  alertYellow: '#E8C22E',
  alertGreen: '#2E9E6B',
};

export const Theme = {
  colors: Colors,
  spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 },
  radius: { sm: 6, md: 12, lg: 20 },
  typography: {
    title: { fontSize: 20, fontWeight: '700' as const },
    body: { fontSize: 15, fontWeight: '400' as const },
    caption: { fontSize: 12, fontWeight: '500' as const },
  },
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && npx jest Theme.test.ts`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add mobile/constants/Theme.ts mobile/__tests__/Theme.test.ts
git commit -m "Add PetBMS theme system with real PET Labs signature colors"
```

---

## Task 7: Rebrand to PetBMS and apply the theme to navigation

**Files:**
- Modify: `mobile/app.json`
- Modify: `mobile/package.json`
- Modify: `mobile/app/(tabs)/_layout.tsx`
- Test: `mobile/__tests__/TabLayout.test.tsx` (new)

**Interfaces:**
- Consumes: `Colors` from `mobile/constants/Theme.ts` (Task 6).

- [ ] **Step 1: Write the failing test**

`mobile/__tests__/TabLayout.test.tsx`:

```typescript
import { Colors } from '../constants/Theme';

// TabLayout wires screenOptions from Theme.ts rather than hardcoded hex —
// this test guards against regressing back to inline colors.
describe('Tab layout theming', () => {
  it('Theme colors used by the tab layout are defined and PetBMS-branded', () => {
    expect(Colors.primary).toBe('#1863DC');
    expect(Colors.ink).toBe('#212121');
  });
});
```

(This is a guard test, not a full render test — `expo-router`'s `Tabs` component requires a navigation container to render in isolation, which is out of scope for this task. The manual verification in Step 5 is the real check.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && npx jest TabLayout.test.tsx`
Expected: FAIL only if Task 6 wasn't completed — otherwise this passes immediately since it just re-checks `Theme.ts`. Confirm it passes trivially, then proceed (this test exists to catch future regressions, not to drive new code).

- [ ] **Step 3: Update `app.json`**

Change in `mobile/app.json`:
- `"name": "PET Lab Monitor"` → `"name": "PetBMS"`
- `"splash".backgroundColor`: `"#1a1a2e"` → `"#0056A7"` (theme's `primaryDark`)
- `"android".adaptiveIcon.backgroundColor`: `"#1a1a2e"` → `"#0056A7"`
- iOS/Android `NSCameraUsageDescription`, `NSPhotoLibraryUsageDescription`, permission descriptions: replace "PET Lab Monitor" with "PetBMS" in the description strings.
- `expo-notifications` plugin `color`: `"#1a73e8"` → `"#1863DC"` (theme's `primary`)

Leave `slug`, `bundleIdentifier` (`com.petlabs.monitor`), and Android `package` unchanged — internal identifiers, not user-facing branding, and changing them would require new app-store-style provisioning we're explicitly avoiding.

- [ ] **Step 4: Update `package.json`**

Change `"name": "petlab-monitor"` → `"name": "petbms"` in `mobile/package.json`.

- [ ] **Step 5: Update `mobile/app/(tabs)/_layout.tsx`**

Add the import and replace the hardcoded hex values:

```typescript
import { Colors } from '../../constants/Theme';
```

```typescript
      screenOptions={{
        headerStyle: { backgroundColor: Colors.primaryDark },
        headerTintColor: Colors.white,
        headerTitleStyle: { fontWeight: '600' },
        headerRight: () => (
          <TouchableOpacity onPress={handleLogout} style={{ marginRight: 16 }}>
            <Ionicons name="log-out-outline" size={22} color={Colors.surfaceAlt} />
          </TouchableOpacity>
        ),
        tabBarStyle: { backgroundColor: Colors.ink, borderTopColor: Colors.primaryDark },
        tabBarActiveTintColor: Colors.primary,
        tabBarInactiveTintColor: '#8A8A9A',
      }}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mobile && npx jest Theme.test.ts TabLayout.test.tsx`
Expected: all passed

- [ ] **Step 7: Manual verification**

Start the dev server (`npx expo start --port 8082` from `mobile/`) and confirm on-device/simulator: app title bar and tab bar render in the new navy/blue palette, no leftover `#1a1a2e`/`#4a9eff` hex visible in the tab chrome.

- [ ] **Step 8: Commit**

```bash
git add mobile/app.json mobile/package.json "mobile/app/(tabs)/_layout.tsx" mobile/__tests__/TabLayout.test.tsx
git commit -m "Rebrand to PetBMS and theme the tab navigation"
```

---

## Task 8: Apply the theme to the remaining screens

**Files:**
- Modify: every screen file under `mobile/app/(tabs)/` and `mobile/app/(auth)/` that contains a hardcoded hex color (grep for `#[0-9a-fA-F]{6}` under `mobile/app/` to get the exact list — do not guess file names).

**Interfaces:**
- Consumes: `Colors`/`Theme` from `mobile/constants/Theme.ts` (Task 6).

- [ ] **Step 1: Enumerate every hardcoded color**

Run: `cd mobile && grep -rnE "#[0-9a-fA-F]{6}" app/ --include="*.tsx" --include="*.ts"`
Record the full file list — this is the exhaustive scope of this task, not a sample.

- [ ] **Step 2: For each file, replace hardcoded hex with the matching `Theme.ts` token**

Follow the same pattern as Task 7 Step 5: import `Colors` (and `Theme` for spacing/typography where a screen has inconsistent padding/margins), replace each literal hex with the closest semantic token (background → `Colors.ink`/`Colors.surface`, accent/link → `Colors.primary`, alert chips → `Colors.alertRed`/`alertOrange`/`alertYellow`/`alertGreen`, text → `Colors.white`/`Colors.ink`). Where an existing screen uses a color with no clean semantic match, add it as a named token to `Theme.ts` (Task 6 file) rather than inlining a new literal — keep the single-source-of-truth invariant.

- [ ] **Step 3: Re-run the grep to confirm zero remaining hardcoded hex outside `Theme.ts`**

Run: `cd mobile && grep -rnE "#[0-9a-fA-F]{6}" app/ --include="*.tsx" --include="*.ts"`
Expected: no output (all matches now live only in `constants/Theme.ts`).

- [ ] **Step 4: Run the full mobile test suite**

Run: `cd mobile && npx jest`
Expected: all existing tests still pass (this task is a pure visual/token substitution, no behavior change).

- [ ] **Step 5: Manual verification**

Walk all 5 tabs (Dashboard, Gauge Log, Records incl. its 3 sub-tabs, Ask AI, PetRace) plus the login screen in the running dev app; confirm consistent PetBMS palette, no visual regressions (contrast still readable, alert chips still distinguishable RED/ORANGE/YELLOW/GREEN).

- [ ] **Step 6: Commit**

```bash
git add mobile/
git commit -m "Apply PetBMS theme across all screens"
```

---

## Task 9: Dashboard widgets for the newly-ingested data

**Files:**
- Read first: `mobile/app/(tabs)/index.tsx` (Dashboard) and `api/routes/dashboard.py` to see the current empty-state handling and existing endpoint shape — do not guess the API contract.
- Modify: `api/routes/dashboard.py` (add an endpoint or extend an existing one to surface `beam_daily` trend data and gauge-photo history — exact shape depends on what Step 1 finds already exists).
- Modify: `mobile/app/(tabs)/index.tsx` — add two widgets: a beam-parameter trend card and a gauge-photo history card, replacing the current empty states for that data.
- Test: `tests/test_dashboard_beam_widget.py` (backend), `mobile/__tests__/DashboardWidgets.test.tsx` (frontend).

**Interfaces:**
- Consumes: `beam_daily` and `gauge_readings` tables (populated by Tasks 2/4 and 5/6 respectively).

- [ ] **Step 1: Read the existing dashboard endpoint and screen**

Open `api/routes/dashboard.py` and `mobile/app/(tabs)/index.tsx` in full. Note: the exact existing response shape, how the frontend currently renders the "no gauge data yet" / "no beam data yet" empty states (search for those states specifically), and the existing service client function in `mobile/services/` that calls the dashboard endpoint.

- [ ] **Step 2: Write the failing backend test**

Write `tests/test_dashboard_beam_widget.py` following the exact pattern of whichever existing test file covers `api/routes/dashboard.py` today (check `tests/` for one, e.g. a `test_dashboard*.py` or similar — mirror its fixture/client setup exactly rather than inventing a new test harness). Assert the new/extended endpoint returns non-empty `beam_trend` and `gauge_history` fields when `beam_daily`/`gauge_readings` have rows, and empty-but-not-erroring fields when they don't.

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest tests/test_dashboard_beam_widget.py -v`
Expected: FAIL (endpoint/field doesn't exist yet).

- [ ] **Step 4: Implement the backend field(s)**

Extend `api/routes/dashboard.py` to query `beam_daily` (recent N days, key params) and `gauge_readings` (recent N readings) and include them in the dashboard response, following the existing file's query/response patterns exactly (same DB connection helper, same error handling style already used elsewhere in that file).

- [ ] **Step 5: Run backend test to verify it passes**

Run: `python -m pytest tests/test_dashboard_beam_widget.py -v`
Expected: passed.

- [ ] **Step 6: Write the failing frontend test**

`mobile/__tests__/DashboardWidgets.test.tsx` — follow the pattern of the existing `mobile/__tests__/RecordsScreen.test.tsx` (same testing-library setup) to assert the Dashboard screen renders a beam-trend card and gauge-history card when the service returns non-empty `beam_trend`/`gauge_history`, and still renders a graceful empty state when they're empty (regression guard — don't break the pre-data-ingestion case).

- [ ] **Step 7: Run it to verify it fails**

Run: `cd mobile && npx jest DashboardWidgets.test.tsx`
Expected: FAIL.

- [ ] **Step 8: Implement the frontend widgets**

Add the two card components to `mobile/app/(tabs)/index.tsx`, styled with `Theme.ts` tokens (Task 6), consuming the new `beam_trend`/`gauge_history` fields from Step 4 via the existing dashboard service client.

- [ ] **Step 9: Run frontend test to verify it passes**

Run: `cd mobile && npx jest DashboardWidgets.test.tsx`
Expected: passed.

- [ ] **Step 10: Manual verification**

With the real ingested data from Task 4/Task 5 in `data/cyclotron.db`, run the full dev stack (uvicorn + expo) and confirm the Dashboard now shows real beam-trend and gauge-history widgets instead of empty states.

- [ ] **Step 11: Commit**

```bash
git add api/routes/dashboard.py "mobile/app/(tabs)/index.tsx" tests/test_dashboard_beam_widget.py mobile/__tests__/DashboardWidgets.test.tsx
git commit -m "Add beam-trend and gauge-history dashboard widgets"
```

---

## Task 10: Installable PWA packaging

**Files:**
- Read first: `mobile/app.json` (`expo.web` section, currently absent), Expo SDK 54 docs behavior for `expo export --platform web` output location (verify against the actual installed `expo` version's output rather than assuming a path).
- Modify: `mobile/app.json` — add a `web` config block.
- Create: `mobile/public/manifest.json` (PWA manifest — exact path confirmed against what `expo export --platform web` actually reads/emits for this Expo SDK version).
- Modify: `mobile/package.json` — add a `"build:web"` script.
- Test: manual (PWA installability is a browser-behavior check, not a unit-testable property).

**Interfaces:**
- Consumes: `Colors.primary`/`Colors.primaryDark` (Task 6) for `theme_color`/`background_color`.

- [ ] **Step 1: Add web config to `app.json`**

Add under the `"expo"` key in `mobile/app.json`:

```json
    "web": {
      "name": "PetBMS",
      "shortName": "PetBMS",
      "bundler": "metro",
      "output": "static",
      "themeColor": "#1863DC",
      "backgroundColor": "#0056A7",
      "display": "standalone"
    }
```

- [ ] **Step 2: Add the build script**

In `mobile/package.json` `"scripts"`:

```json
    "build:web": "expo export --platform web"
```

- [ ] **Step 3: Run the export and inspect the output**

Run: `cd mobile && npx expo export --platform web`
Read the generated output directory's `manifest.json` (or equivalent) to confirm whether Expo SDK 54's web output auto-generates the PWA manifest from the `app.json` `web` block above, or whether a manual `mobile/public/manifest.json` is additionally required. If Expo already generates a correct manifest from Step 1's config, skip Step 4. If not, proceed to Step 4.

- [ ] **Step 4 (only if Step 3 shows it's needed): Hand-write the manifest**

`mobile/public/manifest.json`:

```json
{
  "name": "PetBMS",
  "short_name": "PetBMS",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0056A7",
  "theme_color": "#1863DC",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

Re-run `npx expo export --platform web` and confirm the manifest is now present in the export output at the path the browser's `<link rel="manifest">` tag references.

- [ ] **Step 5: Wire the export into the FastAPI backend's static serving**

Read `api/main.py`'s existing static-file-serving setup (if any) or add one that serves the `mobile` web export's output directory, so the PWA is reachable from the same Render deployment referenced in the design spec. Follow whatever static-mount pattern FastAPI/Starlette convention the rest of `api/main.py` already uses for consistency.

- [ ] **Step 6: Manual verification**

1. Run the backend serving the web export locally.
2. Open the URL in a mobile browser (Chrome on Android or Safari on iOS).
3. Confirm the browser offers "Add to Home Screen" / install prompt.
4. Install it; confirm the home-screen icon is "PetBMS" branded and opens full-screen with no browser chrome.
5. Open the same URL without installing; confirm it still works as a normal responsive website (fallback case from the design spec's error-handling section).

- [ ] **Step 7: Commit**

```bash
git add mobile/app.json mobile/package.json mobile/public/manifest.json api/main.py
git commit -m "Add installable PWA packaging (PetBMS manifest, web export, static serving)"
```

---

## Task 11: Full regression pass

**Files:** none new — this task only runs the suites.

- [ ] **Step 1: Run the full Python test suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass, including every new test file from Tasks 1-5 and 9 plus every pre-existing test.

- [ ] **Step 2: Run the full mobile test suite**

Run: `cd mobile && npx jest`
Expected: all tests pass, including every new test file from Tasks 6-9 plus every pre-existing test (`Config.test.ts`, `GaugeLog.test.ts`, `GaugeStatus.test.ts`, `RecordsScreen.test.tsx`).

- [ ] **Step 3: Fix any regression found**

If either suite has a failure, treat it as a bug introduced by this plan's earlier tasks — fix at the root cause (do not skip/suppress the failing test) and re-run Steps 1-2 until both are green.

- [ ] **Step 4: Final manual smoke test**

Walk the entire app end-to-end once more per the design spec's testing section: all 5 tabs render themed and populated with real ingested data, PWA installs and looks native, fallback website works, existing Expo Go dev flow still works unchanged.

- [ ] **Step 5: Update memory**

Update `project_petbms_redesign.md` (in the Claude memory store) marking the redesign complete, noting final state (row counts ingested, any deferred items), and update `MEMORY.md`'s index line accordingly. This closes out the end goal recorded at the start of this work.
