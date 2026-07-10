"""TDD: the cloud DB schema is missing beam_daily entirely, unlike the local
db.py schema. api/routes/dashboard.py's _beam_trend() already tolerates this
gracefully (returns [] on sqlite3.OperationalError), but that means the table
needs to actually exist for the beam-trend widget to ever show real data on a
Render deploy - ingestion never runs there, only a one-time data push does.
"""
import sqlite3

from api.db_cloud import init_cloud_tables

REQUIRED_COLUMNS = {'date', 'param', 'mean', 'std', 'min', 'max', 'p10', 'p90', 'data_quality'}


def test_beam_daily_table_exists_after_init(tmp_path):
    db = str(tmp_path / 'test.db')
    init_cloud_tables(db)
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(beam_daily)").fetchall()}
    conn.close()
    missing = REQUIRED_COLUMNS - cols
    assert not missing, f"beam_daily missing columns: {sorted(missing)}"


def test_beam_daily_upsert_is_keyed_on_date_and_param(tmp_path):
    db = str(tmp_path / 'test.db')
    init_cloud_tables(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO beam_daily VALUES (?,?,?,?,?,?,?,?,?)",
        ['2026-01-01', 'Arc-I', 1.0, 0.1, 0.5, 1.5, 0.6, 1.4, 'ok'],
    )
    conn.execute(
        "INSERT OR REPLACE INTO beam_daily VALUES (?,?,?,?,?,?,?,?,?)",
        ['2026-01-01', 'Arc-I', 2.0, 0.2, 0.5, 1.5, 0.6, 1.4, 'ok'],
    )
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM beam_daily").fetchone()[0]
    mean = conn.execute("SELECT mean FROM beam_daily WHERE date=? AND param=?", ['2026-01-01', 'Arc-I']).fetchone()[0]
    conn.close()
    assert rows == 1
    assert mean == 2.0
