"""Ingest PETrace 800 log files into the cyclotron DB (petrace_batches table)."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from monitor.petrace_parser import parse_log


def ingest_log_dir(log_dir: str, db_path: str) -> dict:
    """Parse every .log file in log_dir and upsert into petrace_batches.

    Returns {inserted, updated, skipped, errors}.
    """
    log_dir = Path(log_dir)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row

    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    inserted = updated = skipped = 0
    errors = []

    for path in sorted(log_dir.glob('*.log')):
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
            r = parse_log(text)
            if not r['batch_no']:
                skipped += 1
                continue

            existing = conn.execute(
                "SELECT batch_no FROM petrace_batches WHERE batch_no = ?",
                [r['batch_no']]
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE petrace_batches SET
                        batch_date=?, tracer_num=?, tracer_name=?, site=?,
                        duration_s=?, row_count=?, foil_no=?,
                        peak_target_uA=?, avg_target_uA=?, total_muAh=?,
                        avg_arc_I=?, avg_vacuum_P=?, peak_vacuum_P=?,
                        rf_efficiency=?, ingested_at=?
                    WHERE batch_no=?
                """, [
                    r['batch_date'], r['tracer_num'], r['tracer_name'], r['site'],
                    r['duration_s'], r['row_count'], r['foil_no'],
                    r['peak_target_uA'], r['avg_target_uA'], r['total_muAh'],
                    r['avg_arc_I'], r['avg_vacuum_P'], r['peak_vacuum_P'],
                    r['rf_efficiency'], now, r['batch_no'],
                ])
                updated += 1
            else:
                conn.execute("""
                    INSERT INTO petrace_batches
                    (batch_no, batch_date, tracer_num, tracer_name, site,
                     duration_s, row_count, foil_no,
                     peak_target_uA, avg_target_uA, total_muAh,
                     avg_arc_I, avg_vacuum_P, peak_vacuum_P,
                     rf_efficiency, ingested_at)
                    VALUES (?,?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?)
                """, [
                    r['batch_no'], r['batch_date'], r['tracer_num'], r['tracer_name'], r['site'],
                    r['duration_s'], r['row_count'], r['foil_no'],
                    r['peak_target_uA'], r['avg_target_uA'], r['total_muAh'],
                    r['avg_arc_I'], r['avg_vacuum_P'], r['peak_vacuum_P'],
                    r['rf_efficiency'], now,
                ])
                inserted += 1
        except Exception as exc:
            errors.append(f'{path.name}: {exc.__class__.__name__}: {exc}')

    conn.commit()
    conn.close()
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped, 'errors': errors}


if __name__ == '__main__':
    import sys
    log_dir = sys.argv[1] if len(sys.argv) > 1 else 'petrace_logs'
    db_path = sys.argv[2] if len(sys.argv) > 2 else 'data/cyclotron.db'
    result = ingest_log_dir(log_dir, db_path)
    print(f"Inserted: {result['inserted']}, Updated: {result['updated']}, "
          f"Skipped: {result['skipped']}")
    if result['errors']:
        for e in result['errors']:
            print(f"  ERROR: {e}")
