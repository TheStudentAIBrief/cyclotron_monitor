"""
TDD: Cofounder Magnehelic gauge data integrity in gauge_readings.

Verifies that all 12 pressure gauges from pet-lab-gauge-app/build_dataset.py
are present in cyclotron.db with correct locations, limits, and readings.

These tests fail (RED) if the seed is missing or incomplete,
and pass (GREEN) after the import-csv seed has been applied.
"""

import sqlite3
import pytest

DB = r"C:\Users\theol\cyclotron_monitor\data\cyclotron.db"
LAB_ID = "petlabs-pretoria"

# Exact register from pet-lab-gauge-app/build_dataset.py
REGISTER = {
    "0063": ("Outside (BFU Secondary)", 130, 250, 10, 390),
    "0091": ("PAL1 to Pharmacy", 8, 20, 5, 25),
    "0092": ("Prep room (BFU HEPA)", 70, 160, 10, 480),
    "0095": ("HVAC room (Production Secondary)", 15, 45, 10, 360),
    "0096": ("HVAC room (Production Primary)", 15, 125, 10, 200),
    "0098": ("HVAC room (Cyclotron Primary)", 15, 45, 10, 200),
    "0099": ("HVAC room (Cyclotron Secondary)", 15, 45, 10, 360),
    "0103": ("HVAC room (Production HEPA)", 180, 250, 10, 480),
    "0104": ("HVAC room (Cyclotron HEPA)", 45, 80, 10, 480),
    "0120": ("Entrance to PAL1", 10, 20, 5, 25),
    "0121": ("Pharmacy to PAL1", -75, -20, -80, -15),
    "0127": ("HVAC room (Bag-in-bag-out filter)", 20, 40, 10, 50),
}


@pytest.fixture(scope="module")
def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_all_twelve_magnehelic_gauges_present(conn):
    """All 12 gauges from build_dataset.py REGISTER appear in gauge_readings."""
    present = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT gauge_name FROM gauge_readings WHERE lab_id=?", (LAB_ID,)
        ).fetchall()
    }
    missing = [g for g in REGISTER if g not in present]
    assert not missing, f"Missing gauges: {missing}"


def test_each_gauge_has_correct_location(conn):
    """Every Magnehelic gauge stores the exact location string from the register."""
    for gauge_id, (location, *_) in REGISTER.items():
        row = conn.execute(
            "SELECT location FROM gauge_readings WHERE gauge_name=? AND lab_id=? LIMIT 1",
            (gauge_id, LAB_ID),
        ).fetchone()
        assert row is not None, f"No rows for gauge {gauge_id}"
        assert row["location"] == location, (
            f"Gauge {gauge_id}: expected '{location}', got '{row['location']}'"
        )


def test_minimum_reading_count(conn):
    """At least 100 Magnehelic readings are seeded (12 gauges × 10+ readings each)."""
    count = conn.execute(
        "SELECT COUNT(*) FROM gauge_readings WHERE gauge_name LIKE '0%' AND lab_id=?",
        (LAB_ID,),
    ).fetchone()[0]
    assert count >= 100, f"Expected ≥100 Magnehelic readings, got {count}"


def test_gauge_0095_has_alert_readings(conn):
    """Gauge 0095 (Production Secondary, alert_hi=45 Pa) has readings above alert threshold."""
    count = conn.execute(
        "SELECT COUNT(*) FROM gauge_readings WHERE gauge_name='0095' AND lab_id=? AND value > 45",
        (LAB_ID,),
    ).fetchone()[0]
    assert count > 0, "Expected ALERT readings for gauge 0095 (value > alert_hi of 45 Pa)"


def test_gauge_0096_verified_photo_reading_present(conn):
    """Gauge 0096 has the 2026-06-24 verified-photo reading of 87 Pa."""
    row = conn.execute(
        "SELECT value, confidence FROM gauge_readings "
        "WHERE gauge_name='0096' AND lab_id=? AND timestamp LIKE '2026-06-24%'",
        (LAB_ID,),
    ).fetchone()
    assert row is not None, "Missing 2026-06-24 reading for gauge 0096"
    assert row["value"] == 87.0, f"Expected 87.0 Pa, got {row['value']}"
    assert row["confidence"] == "verified-photo"


def test_gauge_0121_negative_pressure_readings(conn):
    """Gauge 0121 (negative-pressure, Pharmacy to PAL1) stores negative Pa values."""
    row = conn.execute(
        "SELECT value FROM gauge_readings WHERE gauge_name='0121' AND lab_id=? AND value < 0 LIMIT 1",
        (LAB_ID,),
    ).fetchone()
    assert row is not None, "No negative-pressure readings for gauge 0121"
    assert row["value"] < 0


def test_all_readings_have_alert_limits(conn):
    """Every Magnehelic reading has non-null alert_lo/alert_hi (required for status badge)."""
    bad = conn.execute(
        "SELECT COUNT(*) FROM gauge_readings "
        "WHERE gauge_name LIKE '0%' AND lab_id=? AND (alert_lo IS NULL OR alert_hi IS NULL)",
        (LAB_ID,),
    ).fetchone()[0]
    assert bad == 0, f"{bad} Magnehelic readings have NULL alert limits — status badge will show UNKNOWN"


def test_all_readings_have_unit_pa(conn):
    """All Magnehelic gauge readings use 'Pa' as the unit."""
    bad = conn.execute(
        "SELECT COUNT(*) FROM gauge_readings "
        "WHERE gauge_name LIKE '0%' AND lab_id=? AND unit != 'Pa'",
        (LAB_ID,),
    ).fetchone()[0]
    assert bad == 0, f"{bad} Magnehelic readings have a unit other than 'Pa'"
