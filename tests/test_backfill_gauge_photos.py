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
