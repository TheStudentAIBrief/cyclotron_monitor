"""
Push local beam_daily / petrace_batches / gauge_readings data to a deployed
cloud instance (e.g. Render) via the /api/admin/import/* endpoints.

The cloud disk starts empty on first deploy - local ingestion never runs
there. This is the one-time (or periodic) bridge for getting real historical
data onto the live site, since there's no shell/file access to the Render
instance to copy the database file directly.

Usage:
    python scripts/push_data_to_cloud.py --api https://petlab-api-qad3.onrender.com

Optional flags:
    --db-path path/to/cyclotron.db   defaults to data/cyclotron.db
    --batch-size N                    rows per request (default 1000, max 2000 server-side)
    --tables beam_daily,gauge_readings,petrace_batches   defaults to all three
"""
import argparse
import math
import os
import pathlib
import sqlite3
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ENDPOINTS = {
    'beam_daily': ('beam-daily', ['date', 'param', 'mean', 'std', 'min', 'max', 'p10', 'p90', 'data_quality']),
    'petrace_batches': ('petrace-batches', [
        'batch_no', 'batch_date', 'tracer_num', 'tracer_name', 'site', 'duration_s',
        'row_count', 'foil_no', 'peak_target_uA', 'avg_target_uA', 'total_muAh',
        'avg_arc_I', 'avg_vacuum_P', 'peak_vacuum_P', 'rf_efficiency', 'ingested_at',
    ]),
    'gauge_readings': ('gauge-readings', [
        'lab_id', 'gauge_name', 'timestamp', 'value', 'unit', 'is_alert', 'alert_reason',
        'photo_path', 'raw_ocr_text', 'location', 'alert_lo', 'alert_hi', 'action_lo',
        'action_hi', 'confidence', 'verified_by', 'verified_at',
    ]),
}


def _read_rows(db_path: str, table: str, columns: list) -> list:
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    cols_sql = ', '.join(columns)
    rows = conn.execute(f'SELECT {cols_sql} FROM {table}').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _push_table(api: str, token: str, table: str, rows: list, batch_size: int) -> int:
    endpoint, _ = _ENDPOINTS[table]
    url = f'{api}/api/admin/import/{endpoint}'
    headers = {'Authorization': f'Bearer {token}'}
    total_inserted = 0
    n_batches = math.ceil(len(rows) / batch_size) if rows else 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        batch_num = i // batch_size + 1
        r = requests.post(url, headers=headers, json={'rows': batch}, timeout=60)
        r.raise_for_status()
        inserted = r.json().get('inserted', 0)
        total_inserted += inserted
        print(f'  [{table}] batch {batch_num}/{n_batches}: {len(batch)} sent, {inserted} inserted/updated')
    return total_inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', required=True, help='Base URL of the deployed API, e.g. https://petlab-api-qad3.onrender.com')
    parser.add_argument('--db-path', default=str(ROOT / 'data' / 'cyclotron.db'))
    parser.add_argument('--batch-size', type=int, default=1000)
    parser.add_argument('--tables', default='beam_daily,gauge_readings,petrace_batches')
    args = parser.parse_args()

    username = os.environ.get('PETLAB_USER')
    password = os.environ.get('PETLAB_PASS')
    if not username or not password:
        print('Set PETLAB_USER and PETLAB_PASS environment variables before running.')
        sys.exit(1)

    print(f'Logging in to {args.api} ...')
    r = requests.post(f'{args.api}/auth/login', data={'username': username, 'password': password}, timeout=30)
    r.raise_for_status()
    token = r.json()['access_token']
    print('Logged in.')

    tables = [t.strip() for t in args.tables.split(',') if t.strip()]
    for table in tables:
        if table not in _ENDPOINTS:
            print(f'Unknown table {table!r}, skipping (known: {list(_ENDPOINTS)})')
            continue
        _, columns = _ENDPOINTS[table]
        print(f'\nReading {table} from {args.db_path} ...')
        rows = _read_rows(args.db_path, table, columns)
        print(f'  {len(rows)} rows found locally.')
        if not rows:
            continue
        total = _push_table(args.api, token, table, rows, args.batch_size)
        print(f'  Done: {total} rows inserted/updated in cloud {table}.')


if __name__ == '__main__':
    main()
