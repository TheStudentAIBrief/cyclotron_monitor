"""Direct-to-DB gauge photo backfill (no running API server required).

For most real-world imports, prefer scripts/import_gauge_photos.py instead --
it downscales images to 1920px before OCR (avoiding Ollama context-size/memory
issues on full-resolution phone photos) and goes through the live, authenticated
/api/gauges/reading endpoint, which is the same code path a real submission
takes. This script exists for cold-start seeding when the API server isn't
running yet (writes to gauge_readings directly via api.db_cloud.get_conn).
"""
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve project root from scripts/ (same pattern as scripts/import_eur_forms.py)
# so `python scripts/backfill_gauge_photos.py` works when invoked directly --
# Python only puts this file's own directory on sys.path, not the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.db_cloud import get_conn  # noqa: E402
from api.routes.gauges import _run_ocr  # noqa: E402
from db import init_db  # noqa: E402


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
