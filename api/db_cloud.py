import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gauge_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lab_id       TEXT    NOT NULL,
    gauge_name   TEXT    DEFAULT '',
    timestamp    TEXT    NOT NULL,
    value        REAL,
    unit         TEXT    DEFAULT '',
    is_alert     INTEGER DEFAULT 0,
    alert_reason TEXT    DEFAULT '',
    photo_path   TEXT    DEFAULT '',
    raw_ocr_text TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_gauge_lab_ts ON gauge_readings(lab_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS push_tokens (
    token         TEXT PRIMARY KEY,
    lab_id        TEXT NOT NULL,
    platform      TEXT DEFAULT '',
    registered_at TEXT NOT NULL
);
"""


def init_cloud_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn
