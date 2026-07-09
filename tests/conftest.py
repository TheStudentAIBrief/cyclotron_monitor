import sqlite3
from datetime import date, timedelta


def make_beam_rows(target_date: date, n_days: int, param: str,
                   base_val: float, slope: float = 0.0):
    rows = []
    for i in range(n_days):
        d = (target_date - timedelta(days=n_days - 1 - i)).isoformat()
        val = base_val + slope * i
        rows.append((d, param, val, 0.01, val - 0.1, val + 0.1,
                     val - 0.05, val + 0.05, 'ok'))
    return rows


def setup_test_db(tmp_path, beam_rows=None, event_rows=None, maint_rows=None, petrace_rows=None):
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
        CREATE TABLE petrace_batches (batch_no INTEGER, batch_date TEXT, tracer_num INTEGER,
            tracer_name TEXT, site TEXT, duration_s REAL, row_count INTEGER, foil_no INTEGER,
            peak_target_uA REAL, avg_target_uA REAL, total_muAh REAL, avg_arc_I REAL,
            avg_vacuum_P REAL, peak_vacuum_P REAL, rf_efficiency REAL, ingested_at TEXT);
    """)
    if beam_rows:
        conn.executemany("INSERT OR REPLACE INTO beam_daily VALUES (?,?,?,?,?,?,?,?,?)", beam_rows)
    if event_rows:
        conn.executemany("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?)", event_rows)
    if maint_rows:
        conn.executemany("INSERT OR REPLACE INTO maintenance_events VALUES (?,?,?,?)", maint_rows)
    if petrace_rows:
        conn.executemany(
            "INSERT INTO petrace_batches "
            "(batch_no, batch_date, total_muAh, rf_efficiency) VALUES (?,?,?,?)",
            petrace_rows)
    conn.commit()
    conn.close()
    return db
