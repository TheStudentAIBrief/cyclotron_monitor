"""
Import EUR (Equipment Usage Record) form photos into the PET Lab database.

Each EUR form is a photographed paper log sheet for the IBA Cyclone 18/9.
Each page typically contains 2-3 operational run entries; each entry yields
4 gauge_readings rows (Gas Flow, Vacuum, IS Current, Beam on Post).

OCR is performed locally via Ollama (qwen2.5vl:7b or equivalent vision model).
Every import is archived to data/gauge_archive/ before DB insertion.

Usage:
    python scripts/import_eur_forms.py path/to/Images.zip
    python scripts/import_eur_forms.py path/to/images/

Environment:
    PETLAB_USER / PETLAB_PASS  — API credentials (not used here; direct DB write)
    OLLAMA_HOST                — default http://localhost:11434
    GAUGE_OLLAMA_MODEL         — default qwen2.5vl:7b
"""
import argparse
import base64
import io
import json
import os
import pathlib
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone

import httpx
from PIL import Image

# Resolve project root from scripts/
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import init_db
from monitor.eur_form_parser import EUR_OCR_PROMPT, EUR_OCR_SCHEMA, parse_eur_response
from monitor.gauge_archive import archive_import

SUPPORTED = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.heic', '.heif'}
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('GAUGE_OLLAMA_MODEL', 'qwen2.5vl:7b').strip()


def _to_jpeg_bytes(raw: bytes, suffix: str, max_px: int = 1200) -> bytes:
    """Convert to JPEG and downscale to max_px on the long edge.

    EUR forms use 1200px (not 1920) — the handwritten text is large enough to read
    at lower resolution, and qwen2.5vl:7b's 4096 default context is exhausted by
    larger images (4032-pixel originals reach ~4114 tokens before the prompt).
    """
    if suffix.lower() in ('.heic', '.heif'):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            print("  HEIC detected — install pillow-heif: pip install pillow-heif")
            raise
    img = Image.open(io.BytesIO(raw))
    img = img.convert('RGB')
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format='JPEG', quality=85)
    return out.getvalue()


def _run_eur_ocr(jpeg_bytes: bytes) -> str:
    """Send image to Ollama with EUR prompt; return raw JSON response string."""
    if os.environ.get("OLLAMA_NEWSLETTER_ONLY") == "1":
        raise RuntimeError("Ollama restricted to newsletter tasks (OLLAMA_NEWSLETTER_ONLY=1)")
    b64 = base64.b64encode(jpeg_bytes).decode()
    r = httpx.post(
        f'{OLLAMA_HOST}/api/generate',
        json={
            'model': OLLAMA_MODEL,
            'prompt': EUR_OCR_PROMPT,
            'images': [b64],
            'stream': False,
            'format': EUR_OCR_SCHEMA,
            'options': {'temperature': 0, 'num_ctx': 4096},
        },
        timeout=600,
    )
    r.raise_for_status()
    return r.json().get('response', '{}')


def _insert_readings(db_path: str, lab_id: str, readings: list[dict]) -> int:
    conn = sqlite3.connect(db_path, timeout=30)
    inserted = 0
    try:
        for row in readings:
            conn.execute(
                "INSERT INTO gauge_readings "
                "(lab_id, gauge_name, timestamp, value, unit, is_alert, alert_reason, "
                "location, alert_lo, alert_hi, action_lo, action_hi, confidence, verified_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    lab_id,
                    row['gauge_name'],
                    row['timestamp'],
                    row['value'],
                    row['unit'],
                    row['is_alert'],
                    row['alert_reason'],
                    row.get('location', 'Control Room'),
                    row.get('alert_lo'),
                    row.get('alert_hi'),
                    row.get('action_lo'),
                    row.get('action_hi'),
                    row.get('confidence', 'eur_form'),
                    row.get('verified_by', ''),
                ],
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def _collect_images(source: pathlib.Path) -> dict[str, bytes]:
    images: dict[str, bytes] = {}
    if source.is_file() and source.suffix.lower() == '.zip':
        with zipfile.ZipFile(source) as zf:
            for name in sorted(zf.namelist()):
                if pathlib.Path(name).suffix.lower() in SUPPORTED and not name.startswith('__'):
                    images[name] = zf.read(name)
    elif source.is_dir():
        for p in sorted(source.rglob('*')):
            if p.suffix.lower() in SUPPORTED:
                images[p.name] = p.read_bytes()
    return images


def main():
    parser = argparse.ArgumentParser(description='Import EUR form photos into gauge_readings.')
    parser.add_argument('source', help='ZIP file or directory of EUR form photos')
    parser.add_argument('--db', help='Path to cyclotron.db (auto-detected from config.json)')
    parser.add_argument('--lab-id', default='', help='Lab identifier (overrides config.json)')
    parser.add_argument('--archive-dir', help='Archive directory (default: data/gauge_archive/)')
    parser.add_argument('--dry-run', action='store_true', help='OCR only; do not insert into DB')
    args = parser.parse_args()

    # Resolve DB path
    db_path = args.db
    lab_id = args.lab_id
    if not db_path:
        cfg_path = ROOT / 'config.json'
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            db_path = cfg.get('db_path', str(ROOT / 'data' / 'cyclotron.db'))
            if not lab_id:
                lab_id = cfg.get('lab_id', 'default')
        else:
            db_path = str(ROOT / 'data' / 'cyclotron.db')
            lab_id = lab_id or 'default'

    archive_dir = args.archive_dir or str(ROOT / 'data' / 'gauge_archive')

    source = pathlib.Path(args.source)
    if not source.exists():
        print(f"Not found: {source}")
        sys.exit(1)

    images = _collect_images(source)
    if not images:
        print("No supported images found.")
        sys.exit(1)

    if not args.dry_run:
        init_db(db_path)
        print(f"DB: {db_path}")

    print(f"Found {len(images)} image(s). Model: {OLLAMA_MODEL}\n")

    total_rows = 0
    total_errors = 0

    for name, raw in images.items():
        suffix = pathlib.Path(name).suffix
        print(f"  {name} ...", end=" ", flush=True)
        try:
            jpeg = _to_jpeg_bytes(raw, suffix)
            ocr_raw = _run_eur_ocr(jpeg)
            readings = parse_eur_response(ocr_raw)

            # Archive regardless of parse result (empty list still gets archived)
            entry_dir = archive_import(name, jpeg, ocr_raw, readings, archive_dir)
            print(f"{len(readings)} readings, archived {os.path.basename(entry_dir)[:30]}", end="")

            if readings and not args.dry_run:
                n = _insert_readings(db_path, lab_id, readings)
                print(f" -> inserted {n}")
                total_rows += n
            else:
                print()
        except Exception as exc:
            print(f"ERROR: {exc.__class__.__name__}: {exc}")
            total_errors += 1

    print(f"\nDone: {total_rows} readings inserted, {total_errors} errors.")
    if args.dry_run:
        print("(dry-run — nothing written to DB)")


if __name__ == '__main__':
    main()
