import sqlite3
import shutil
from pathlib import Path
from ingest import ingest_all

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_ingest_populates_beam_daily(tmp_path):
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
    ingest_all(str(tmp_path / "logs"), db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM maintenance_events").fetchone()[0]
    conn.close()
    assert rows == 2, "Second ingest must not duplicate rows (2 unique events in fixture)"
