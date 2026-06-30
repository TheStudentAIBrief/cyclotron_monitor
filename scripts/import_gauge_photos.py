"""
Import gauge reading photos from a ZIP file or directory into the PET Lab Monitor API.

The API runs OCR (qwen2.5vl:7b) on each photo to extract the reading.

Usage:
    python scripts/import_gauge_photos.py photos.zip
    python scripts/import_gauge_photos.py path/to/photos/

Optional flags:
    --gauge-name "Vacuum Pump"   label every photo with this gauge name
    --api http://192.168.4.46:8000
"""
import argparse
import base64
import io
import pathlib
import sys
import zipfile

import requests

SUPPORTED = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.heic', '.heif'}
API = "http://192.168.4.46:8000"


def _to_jpeg_bytes(raw: bytes, suffix: str) -> bytes:
    """Convert any image format (incl. HEIC) to JPEG bytes."""
    if suffix.lower() in ('.heic', '.heif'):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            print("  HEIC detected — install pillow-heif: pip install pillow-heif")
            raise
    from PIL import Image
    img = Image.open(io.BytesIO(raw))
    img = img.convert('RGB')
    # Downscale to max 1920px on the long edge (matches cofounder's vision.py)
    max_px = 1920
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format='JPEG', quality=85)
    return out.getvalue()


def process_images(image_map: dict[str, bytes], token: str, gauge_name: str, api: str):
    """image_map: {filename: raw_bytes}. Returns list of result dicts."""
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    for name, raw in image_map.items():
        suffix = pathlib.Path(name).suffix
        print(f"  Processing {name} ...", end=" ", flush=True)
        try:
            jpeg = _to_jpeg_bytes(raw, suffix)
            b64 = base64.b64encode(jpeg).decode()
            r = requests.post(
                f"{api}/api/gauges/reading",
                headers=headers,
                json={"photo_b64": b64, "gauge_name": gauge_name or pathlib.Path(name).stem},
                timeout=120,
            )
            r.raise_for_status()
            res = r.json()
            value = res.get('value')
            unit  = res.get('unit', '')
            ocr   = res.get('raw_ocr_text', '')
            print(f"value={value} {unit}  — {ocr[:80]}")
            results.append({"file": name, "ok": True, **res})
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append({"file": name, "ok": False, "error": str(exc)})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="ZIP file or directory of photos")
    parser.add_argument("--gauge-name", default="", help="Label for all photos")
    parser.add_argument("--api", default=API, help="API base URL")
    args = parser.parse_args()

    source = pathlib.Path(args.source)
    if not source.exists():
        print(f"Not found: {source}")
        sys.exit(1)

    # Login — credentials must come from the environment (no default credentials).
    import os
    username = os.environ.get("PETLAB_USER")
    password = os.environ.get("PETLAB_PASS")
    if not username or not password:
        print("Set PETLAB_USER and PETLAB_PASS environment variables before importing.")
        sys.exit(1)
    r = requests.post(f"{args.api}/auth/login",
                      data={"username": username, "password": password})
    r.raise_for_status()
    token = r.json()["access_token"]

    # Collect images
    image_map: dict[str, bytes] = {}
    if source.is_file() and source.suffix.lower() == '.zip':
        with zipfile.ZipFile(source) as zf:
            for name in sorted(zf.namelist()):
                if pathlib.Path(name).suffix.lower() in SUPPORTED and not name.startswith('__'):
                    image_map[name] = zf.read(name)
    elif source.is_dir():
        for p in sorted(source.rglob("*")):
            if p.suffix.lower() in SUPPORTED:
                image_map[p.name] = p.read_bytes()
    else:
        print(f"Unsupported source: {source} (need .zip or directory)")
        sys.exit(1)

    if not image_map:
        print("No supported images found (jpg/png/heic/webp/bmp/gif)")
        sys.exit(1)

    print(f"Found {len(image_map)} image(s). Importing via {args.api} ...\n")
    results = process_images(image_map, token, args.gauge_name, args.api)

    ok = sum(1 for r in results if r['ok'])
    failed = len(results) - ok
    print(f"\nDone: {ok} imported, {failed} failed.")
    if failed:
        for r in results:
            if not r['ok']:
                print(f"  FAILED: {r['file']} — {r.get('error','?')}")


if __name__ == "__main__":
    main()
