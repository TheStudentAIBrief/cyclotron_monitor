"""
TDD tests for gauge_readings table schema completeness.

The on-prem db.py schema was missing 8 columns added by the cloud db_cloud.py
migration system. The import-csv route and EUR form import both reference these
columns. A fresh init_db() must include all 18 columns.
"""
import sqlite3
import pytest
from db import init_db

REQUIRED_COLUMNS = {
    # Core columns (present from the start)
    'id', 'lab_id', 'gauge_name', 'timestamp', 'value', 'unit',
    'is_alert', 'alert_reason', 'photo_path', 'raw_ocr_text',
    # Extended columns (cofounder CSV format + EUR form)
    'location', 'alert_lo', 'alert_hi', 'action_lo', 'action_hi',
    'confidence', 'verified_by', 'verified_at',
}


def test_all_required_columns_present_after_init(tmp_path):
    db = str(tmp_path / 'test.db')
    init_db(db)
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(gauge_readings)").fetchall()}
    conn.close()
    missing = REQUIRED_COLUMNS - cols
    assert not missing, f"gauge_readings missing columns: {sorted(missing)}"


def test_existing_db_gets_columns_added(tmp_path):
    """init_db() called on an older DB (without the 8 extended cols) upgrades it."""
    db = str(tmp_path / 'old.db')
    # Create DB with only the core columns
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE gauge_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lab_id TEXT NOT NULL,
            gauge_name TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            value REAL,
            unit TEXT DEFAULT '',
            is_alert INTEGER DEFAULT 0,
            alert_reason TEXT DEFAULT '',
            photo_path TEXT DEFAULT '',
            raw_ocr_text TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()

    # Run init_db — should apply migrations
    init_db(db)

    conn2 = sqlite3.connect(db)
    cols = {row[1] for row in conn2.execute("PRAGMA table_info(gauge_readings)").fetchall()}
    conn2.close()
    missing = REQUIRED_COLUMNS - cols
    assert not missing, f"Migration failed, still missing: {sorted(missing)}"


def test_init_db_idempotent(tmp_path):
    """Calling init_db twice on the same DB must not raise."""
    db = str(tmp_path / 'test.db')
    init_db(db)
    init_db(db)  # should not raise
