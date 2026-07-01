"""
TDD tests for scripts/generate_gauge_qr_labels.py (not yet implemented).

Generates one QR-labeled PDF per gauge (plus a combined multi-page PDF) so gauges
can be physically labeled and scanned into Johannes's separate eQMS system later.
The script itself only ever runs SELECTs against gauge_readings — these tests use
a throwaway tmp_path SQLite file and NEVER touch the real production DB at
C:\\Users\\theol\\cyclotron_monitor\\data\\cyclotron.db.

Covers:
- fetch_gauges(db_path): most-recent row per (gauge_name, location), excluding
  rows with an empty gauge_name or empty location.
- gauge_scan_url(base_url, gauge_name): exact scan-URL string format.
- build_qr(url): returns a qrcode.QRCode whose encoded data reconstructs to the
  exact URL (introspected via qr.data_list, not an image decoder).
- render_label_image(gauge_row, qr_image): composes a label PIL.Image that is
  strictly larger than the raw QR image (room for gauge text).
- write_individual_pdf / write_combined_pdf: real Pillow PDF files get written
  (non-empty), and write_combined_pdf's Image.save call receives one entry in
  append_images per extra page.
- main(db_path, base_url, output_dir): orchestrates everything; the combined
  PDF is built from exactly as many images as fetch_gauges() returns rows.
"""
import sqlite3

import pytest
from PIL import Image as PILImage
import qrcode

from scripts.generate_gauge_qr_labels import (
    build_qr,
    fetch_gauges,
    gauge_scan_url,
    main,
    render_label_image,
    write_combined_pdf,
    write_individual_pdf,
)

SCHEMA = """
CREATE TABLE gauge_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lab_id       TEXT    NOT NULL,
    gauge_name   TEXT    DEFAULT '',
    timestamp    TEXT    NOT NULL,
    value        REAL,
    unit         TEXT    DEFAULT '',
    is_alert     INTEGER DEFAULT 0,
    alert_reason TEXT    DEFAULT '',
    photo_path   TEXT    DEFAULT '',
    raw_ocr_text TEXT    DEFAULT '',
    location     TEXT    DEFAULT '',
    alert_lo     REAL,
    alert_hi     REAL,
    action_lo    REAL,
    action_hi    REAL,
    confidence   TEXT    DEFAULT '',
    verified_by  TEXT    DEFAULT '',
    verified_at  TEXT    DEFAULT ''
);
"""

ROWS = [
    # (gauge_name, timestamp, value, unit, location, alert_lo, alert_hi, action_lo, action_hi, confidence)
    # G-101: two readings for the SAME gauge/location — only the later timestamp should survive.
    ("G-101", "2026-06-29T08:00:00", 5.2, "bar", "Control Room", 1.0, 9.0, 0.5, 9.5, "ok"),
    ("G-101", "2026-06-30T09:15:00", 6.1, "bar", "Control Room", 1.0, 9.0, 0.5, 9.5, "ok"),
    # G-102: single reading, different location.
    ("G-102", "2026-06-30T09:00:00", 2.3, "Pa", "HVAC Room - Cyclotron HEPA", 0.0, 5.0, -1.0, 6.0, "ok"),
    # Junk row: empty location -> must be excluded.
    ("G-103", "2026-06-30T09:00:00", 1.0, "bar", "", 0.0, 1.0, 0.0, 1.0, "low"),
    # Junk row: empty gauge_name -> must be excluded.
    ("", "2026-06-30T09:00:00", 1.0, "bar", "Prep room", 0.0, 1.0, 0.0, 1.0, "low"),
]


def make_gauge_db(tmp_path):
    db_path = str(tmp_path / "gauges_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executemany(
        """
        INSERT INTO gauge_readings
            (lab_id, gauge_name, timestamp, value, unit, location,
             alert_lo, alert_hi, action_lo, action_hi, confidence)
        VALUES ('petlabs-pretoria', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]) for r in ROWS],
    )
    conn.commit()
    conn.close()
    return db_path


# ── fetch_gauges ──────────────────────────────────────────────────────────

def test_fetch_gauges_excludes_empty_gauge_name_and_location(tmp_path):
    db_path = make_gauge_db(tmp_path)
    rows = fetch_gauges(db_path)
    names = {r["gauge_name"] for r in rows}
    assert "" not in names
    assert "G-103" not in names  # dropped for empty location
    assert names == {"G-101", "G-102"}


def test_fetch_gauges_uses_only_latest_reading_per_gauge(tmp_path):
    db_path = make_gauge_db(tmp_path)
    rows = {r["gauge_name"]: r for r in fetch_gauges(db_path)}
    assert len(fetch_gauges(db_path)) == 2  # no duplicate G-101 rows

    g101 = rows["G-101"]
    assert g101["timestamp"] == "2026-06-30T09:15:00"
    assert g101["value"] == 6.1
    assert g101["location"] == "Control Room"


def test_fetch_gauges_returns_expected_fields(tmp_path):
    db_path = make_gauge_db(tmp_path)
    rows = {r["gauge_name"]: r for r in fetch_gauges(db_path)}

    g102 = rows["G-102"]
    expected = {
        "gauge_name": "G-102",
        "location": "HVAC Room - Cyclotron HEPA",
        "unit": "Pa",
        "value": 2.3,
        "alert_lo": 0.0,
        "alert_hi": 5.0,
        "action_lo": -1.0,
        "action_hi": 6.0,
        "confidence": "ok",
        "timestamp": "2026-06-30T09:00:00",
    }
    for key, value in expected.items():
        assert g102[key] == value, f"{key}: expected {value!r}, got {g102.get(key)!r}"


# ── gauge_scan_url ────────────────────────────────────────────────────────

def test_gauge_scan_url_builds_expected_string():
    url = gauge_scan_url("http://192.168.4.46:8000", "G-101")
    assert url == "http://192.168.4.46:8000/scan/G-101"


def test_gauge_scan_url_no_trailing_slash_inserted():
    url = gauge_scan_url("http://192.168.4.46:8000", "HVAC-Primary-01")
    assert url.count("//") == 1  # only the scheme's "//", no accidental double slash
    assert url == "http://192.168.4.46:8000/scan/HVAC-Primary-01"


# ── build_qr ──────────────────────────────────────────────────────────────

def test_build_qr_encodes_exact_url():
    url = "http://192.168.4.46:8000/scan/G-101"
    qr = build_qr(url)

    assert isinstance(qr, qrcode.QRCode)
    # Introspect the qrcode library's own parsed segments rather than decoding
    # a rendered image — qr.add_data() may split the payload into multiple
    # QRData segments depending on the optimize setting, so reconstruct the
    # full encoded byte string from every segment before comparing.
    encoded = b"".join(segment.data for segment in qr.data_list)
    assert encoded == url.encode("utf-8")


def test_build_qr_different_gauges_encode_different_urls():
    qr_a = build_qr("http://192.168.4.46:8000/scan/G-101")
    qr_b = build_qr("http://192.168.4.46:8000/scan/G-102")
    data_a = b"".join(s.data for s in qr_a.data_list)
    data_b = b"".join(s.data for s in qr_b.data_list)
    assert data_a != data_b


# ── render_label_image ───────────────────────────────────────────────────

def test_render_label_image_returns_larger_canvas_than_qr(tmp_path):
    db_path = make_gauge_db(tmp_path)
    gauge_row = fetch_gauges(db_path)[0]
    scan_url = gauge_scan_url("http://192.168.4.46:8000", gauge_row["gauge_name"])
    qr_image = qrcode.make(scan_url)

    label_image = render_label_image(gauge_row, qr_image)

    assert isinstance(label_image, PILImage.Image)
    # The label must have room for gauge text in addition to the QR code itself.
    assert label_image.size[0] >= qr_image.size[0]
    assert label_image.size[1] > qr_image.size[1]


# ── write_individual_pdf / write_combined_pdf ────────────────────────────

def test_write_individual_pdf_creates_nonempty_file(tmp_path):
    image = PILImage.new("RGB", (200, 240), "#1a1a2e")
    out_path = tmp_path / "G-101.pdf"

    write_individual_pdf(image, str(out_path))

    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_write_combined_pdf_creates_nonempty_file(tmp_path):
    images = [PILImage.new("RGB", (200, 240), "#1a1a2e") for _ in range(3)]
    out_path = tmp_path / "combined.pdf"

    write_combined_pdf(images, str(out_path))

    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_write_combined_pdf_appends_all_but_first_image(tmp_path, monkeypatch):
    images = [PILImage.new("RGB", (200, 240), "#1a1a2e") for _ in range(4)]
    out_path = tmp_path / "combined.pdf"

    calls = []
    original_save = PILImage.Image.save

    def spy_save(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(PILImage.Image, "save", spy_save)

    write_combined_pdf(images, str(out_path))

    assert len(calls) == 1, "expected exactly one Image.save() call for the combined PDF"
    _, kwargs = calls[0]
    assert kwargs.get("save_all") is True
    append_images = kwargs.get("append_images")
    assert append_images is not None
    assert len(append_images) == len(images) - 1


# ── main ──────────────────────────────────────────────────────────────────

def test_main_writes_combined_pdf_with_one_image_per_fetched_gauge(tmp_path, monkeypatch):
    db_path = make_gauge_db(tmp_path)
    expected_count = len(fetch_gauges(db_path))
    assert expected_count == 2  # sanity check on the fixture data

    captured = {}
    import scripts.generate_gauge_qr_labels as module

    def fake_write_combined_pdf(label_images, path):
        captured["count"] = len(label_images)

    monkeypatch.setattr(module, "write_combined_pdf", fake_write_combined_pdf)

    output_dir = tmp_path / "labels_out"
    main(db_path, "http://192.168.4.46:8000", str(output_dir))

    assert captured.get("count") == expected_count
