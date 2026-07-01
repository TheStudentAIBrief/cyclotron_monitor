"""
Generate one QR-labeled PDF per gauge (plus a combined multi-page PDF) so the
16 real gauges can be physically labeled and scanned into Johannes's separate
eQMS system later. Read-only against gauge_readings (SELECT only).

Usage:
    python scripts/generate_gauge_qr_labels.py [--db-path PATH] [--base-url URL] [--output-dir DIR]

--db-path defaults to api.config.get_config()['db_path'] (the real DB, only
resolved when this script is actually run — never during import/collection).
--base-url defaults to auto-detecting this machine's LAN IP (port 8000).
--output-dir defaults to "qr_labels" at the repo root.
"""
import os
import sqlite3

import qrcode
from PIL import Image, ImageDraw, ImageFont

LABEL_BG = "#1a1a2e"


def fetch_gauges(db_path):
    """Return one dict per gauge: the latest reading, excluding rows with an
    empty gauge_name or empty location."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT gauge_name, location, unit, value, alert_lo, alert_hi,
                   action_lo, action_hi, confidence, timestamp
            FROM gauge_readings
            WHERE gauge_name != '' AND location != ''
            ORDER BY timestamp ASC
            """
        ).fetchall()
    finally:
        conn.close()

    latest = {}
    for row in rows:
        latest[row["gauge_name"]] = dict(row)
    return list(latest.values())


def gauge_scan_url(base_url, gauge_name):
    return f"{base_url.rstrip('/')}/scan/{gauge_name}"


def build_qr(url):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr


def render_label_image(gauge_row, qr_image):
    """Compose a navy-background label: gauge name banner, QR code, location caption."""
    qr_rgb = qr_image.convert("RGB")
    w, h = qr_rgb.size
    top_banner = 50
    bottom_banner = 40

    canvas = Image.new("RGB", (w, h + top_banner + bottom_banner), LABEL_BG)
    canvas.paste(qr_rgb, (0, top_banner))

    draw = ImageDraw.Draw(canvas)
    try:
        title_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
        caption_font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13)
    except OSError:
        title_font = ImageFont.load_default()
        caption_font = title_font

    draw.text((w // 2, top_banner // 2), gauge_row["gauge_name"],
               fill="white", font=title_font, anchor="mm")
    draw.text((w // 2, h + top_banner + bottom_banner // 2), gauge_row["location"],
               fill="white", font=caption_font, anchor="mm")

    return canvas


def write_individual_pdf(image, out_path):
    image.convert("RGB").save(out_path, "PDF")


def write_combined_pdf(images, out_path):
    rgb_images = [img.convert("RGB") for img in images]
    rgb_images[0].save(out_path, "PDF", save_all=True, append_images=rgb_images[1:])


def main(db_path, base_url, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    gauges = fetch_gauges(db_path)
    label_images = []
    for gauge in gauges:
        url = gauge_scan_url(base_url, gauge["gauge_name"])
        qr = build_qr(url)
        qr_image = qr.make_image(fill_color="white", back_color=LABEL_BG)
        label_image = render_label_image(gauge, qr_image)
        label_images.append(label_image)

        safe_name = gauge["gauge_name"].replace("/", "_").replace("\\", "_")
        write_individual_pdf(label_image, os.path.join(output_dir, f"{safe_name}.pdf"))

    if label_images:
        write_combined_pdf(label_images, os.path.join(output_dir, "combined.pdf"))

    return label_images


def _detect_lan_ip():
    """Find the primary outbound-routing local IP without sending any traffic."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


if __name__ == "__main__":
    import argparse
    import pathlib

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None,
                         help="Path to cyclotron.db (default: api.config.get_config()['db_path'])")
    parser.add_argument("--base-url", default=None,
                         help="Base URL for scan links, e.g. http://192.168.4.46:8000 "
                              "(default: auto-detect this machine's LAN IP on port 8000)")
    parser.add_argument("--output-dir", default=str(pathlib.Path(__file__).resolve().parent.parent / "qr_labels"),
                         help="Directory to write label PDFs into (default: qr_labels/ at repo root)")
    args = parser.parse_args()

    resolved_db_path = args.db_path
    if resolved_db_path is None:
        from api.config import get_config
        resolved_db_path = get_config()["db_path"]

    resolved_base_url = args.base_url
    if resolved_base_url is None:
        resolved_base_url = f"http://{_detect_lan_ip()}:8000"

    result = main(resolved_db_path, resolved_base_url, args.output_dir)
    print(f"Wrote {len(result)} individual label PDFs and combined.pdf to {args.output_dir}")
