import os
import sqlite3
from pathlib import Path
from parsers.beam_parser import parse_beam_file, aggregate_daily
from parsers.hyper_parser import parse_hyper_file
from parsers.maintenance_labels import extract_maintenance_events
from db import init_db, upsert_beam_daily, insert_events, upsert_maintenance_event


def ingest_all(log_dir: str, db_path: str) -> dict:
    init_db(db_path)
    stats = {'beam_files': 0, 'hyper_files': 0, 'events': 0, 'maintenance_events': 0}

    all_files = sorted(os.listdir(log_dir))
    beam_files = [f for f in all_files if f.endswith('.log') and 'beam' in f]
    hyper_files = [f for f in all_files if f.endswith('.log') and
                   ('hyper' in f or 'ui' in f)]

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        for i, filename in enumerate(beam_files):
            try:
                fpath = Path(log_dir) / filename
                if fpath.is_symlink():
                    print(f"  WARN beam {filename}: skipping symlink")
                    continue
                df = parse_beam_file(str(fpath))
                daily = aggregate_daily(df)
                for d, row in daily.iterrows():
                    params = [c[:-5] for c in row.index if c.endswith('_mean')]
                    for param in params:
                        stats_dict = {
                            'mean': row.get(f'{param}_mean'),
                            'std':  row.get(f'{param}_std'),
                            'min':  row.get(f'{param}_min'),
                            'max':  row.get(f'{param}_max'),
                            'p10':  row.get(f'{param}_p10'),
                            'p90':  row.get(f'{param}_p90'),
                        }
                        upsert_beam_daily(conn, str(d), param, stats_dict,
                                          str(row.get('data_quality', 'ok')))
                stats['beam_files'] += 1
            except Exception as e:
                print(f"  WARN beam {filename}: {e}")
            if (i + 1) % 10 == 0:
                conn.commit()

        for i, filename in enumerate(hyper_files):
            try:
                fpath = Path(log_dir) / filename
                if fpath.is_symlink():
                    print(f"  WARN hyper {filename}: skipping symlink")
                    continue
                df = parse_hyper_file(str(fpath))
                if not df.empty:
                    rows = [
                        (str(r['timestamp']), r['severity'], r['code'],
                         r['function'], r['message'], r['source_file'])
                        for _, r in df.iterrows()
                    ]
                    insert_events(conn, rows)
                    stats['events'] += len(rows)
                stats['hyper_files'] += 1
            except Exception as e:
                print(f"  WARN hyper {filename}: {e}")
            if (i + 1) % 10 == 0:
                conn.commit()

        maint_df = extract_maintenance_events(log_dir)
        for _, row in maint_df.iterrows():
            upsert_maintenance_event(conn, str(row['timestamp']),
                                     row['component_key'], row['component_label'],
                                     row['source_file'])
        stats['maintenance_events'] = len(maint_df)

        conn.commit()
    finally:
        conn.close()
    return stats
