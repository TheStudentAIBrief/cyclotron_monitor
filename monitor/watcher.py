import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from parsers.beam_parser import parse_beam_file, aggregate_daily
from parsers.hyper_parser import parse_hyper_file
from parsers.maintenance_labels import extract_maintenance_events, extract_from_file
from db import init_db, upsert_beam_daily, insert_events, upsert_maintenance_event, prune_events
from monitor.cloud_sync import sync_if_configured

_log = logging.getLogger('cyclotron.watcher')


class _Handler(FileSystemEventHandler):
    def __init__(self, on_file):
        self._on_file = on_file

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.log'):
            self._on_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.log'):
            self._on_file(event.src_path)


class LogWatcher:
    def __init__(self, log_dir: str, on_file=None):
        self._log_dir = log_dir
        self._on_file = on_file or (lambda p: None)
        self._observer = Observer()

    def start(self):
        handler = _Handler(self._on_file)
        self._observer.schedule(handler, self._log_dir, recursive=False)
        self._observer.start()

    def stop(self):
        self._observer.stop()
        self._observer.join()


def start_monitor(log_dir, db_path, model_dir, dashboard_path, alert_path):
    from features.engineer import build_features
    from models.counter import get_counter_days, FOILS_LABELS
    from models.predictor import predict
    from monitor.dashboard_writer import write_dashboard
    from datetime import date as date_type

    init_db(db_path)
    # Keyed by path → (mtime, size) at last ingest. A set would skip all later writes to the
    # same file (the Siemens controller appends to log files continuously).
    processed: dict[str, tuple[float, int]] = {}
    COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1', 'TRANSFER LINES']

    _last_refresh_time = 0.0
    _MIN_REFRESH_INTERVAL = 120.0  # seconds — prevents CPU spikes during rapid log bursts
    # Mutex prevents concurrent refreshes from the watchdog thread and the main loop.
    _refresh_lock = threading.Lock()

    def _refresh():
        nonlocal _last_refresh_time
        if not _refresh_lock.acquire(blocking=False):
            return  # a refresh is already running
        try:
            _last_refresh_time = time.monotonic()

            conn_r = sqlite3.connect(db_path, timeout=30)
            try:
                row_d = conn_r.execute("SELECT MAX(date) FROM beam_daily").fetchone()
            finally:
                conn_r.close()
            target_date = date_type.fromisoformat(row_d[0]) if row_d and row_d[0] else date_type.today()

            preds = []
            for comp in COMPONENTS:
                try:
                    feats = build_features(target_date, comp, db_path)
                    counter_days, _ = get_counter_days(comp, db_path)
                    conn = sqlite3.connect(db_path, timeout=30)
                    try:
                        if comp == 'FOILS':
                            ph = ','.join('?' * len(FOILS_LABELS))
                            row = conn.execute(
                                f"SELECT MAX(date(timestamp)) FROM maintenance_events "
                                f"WHERE component_label IN ({ph})",
                                list(FOILS_LABELS)
                            ).fetchone()
                        else:
                            row = conn.execute(
                                "SELECT MAX(date(timestamp)) FROM maintenance_events "
                                "WHERE component_label=?",
                                [comp]
                            ).fetchone()
                    finally:
                        conn.close()
                    last_maint = row[0] if row and row[0] else 'Unknown'
                    preds.append(predict(comp, feats, model_dir, counter_days, last_maint))
                except Exception as e:
                    _log.error('Prediction failed for %s: %s', comp, e)

            if not preds:
                _log.error('All component predictions failed — dashboard not updated')
                return

            write_dashboard(preds, dashboard_path, alert_path)

            log_conn = sqlite3.connect(db_path, timeout=30)
            try:
                for result in preds:
                    log_conn.execute(
                        "INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?)",
                        [target_date.isoformat(), result.component, round(result.risk_score, 3),
                         round(result.days_estimate, 1), result.alert_level,
                         result.primary_signal, json.dumps(result.top_reasons)]
                    )
                log_conn.commit()
            finally:
                log_conn.close()

            # Evict processed entries for files that have been rotated away to prevent
            # the dict growing unboundedly over months of continuous operation.
            stale = [p for p in list(processed) if not Path(p).exists()]
            for p in stale:
                del processed[p]

            _log.info('Dashboard updated (%d components)', len(preds))
            sync_if_configured(dashboard_path)

            # Archive then prune old events after each successful refresh.
            # archive_dir sits next to the DB; individual monthly .csv.gz files
            # are written before any rows are deleted (NNR audit trail requirement).
            archive_dir = str(Path(db_path).parent / 'events_archive')
            pruned = prune_events(db_path, archive_dir=archive_dir)
            if pruned:
                _log.info('Pruned %s old events from DB', f'{pruned:,}')
        finally:
            _refresh_lock.release()

    def _process_file(path):
        nonlocal _last_refresh_time
        file_path = Path(path)
        # Reject symlinks and files that watchdog somehow reports outside log_dir.
        if file_path.is_symlink():
            _log.warning('Ignoring symlink in log directory: %s', file_path.name)
            return
        try:
            file_path.resolve().relative_to(Path(log_dir).resolve())
        except ValueError:
            _log.warning('File reported outside log directory, ignoring: %s', file_path.name)
            return
        # Skip if the file's mtime+size haven't changed since last ingest (dedup).
        try:
            st = file_path.stat()
            stamp = (st.st_mtime, st.st_size)
        except OSError:
            return
        if processed.get(path) == stamp:
            return
        processed[path] = stamp
        name = file_path.name
        try:
            conn = sqlite3.connect(db_path, timeout=30)
            try:
                if 'beam' in name:
                    df = parse_beam_file(path)
                    daily = aggregate_daily(df)
                    for d, row in daily.iterrows():
                        params = [c[:-5] for c in row.index if c.endswith('_mean')]
                        for param in params:
                            stats = {k: row.get(f'{param}_{k}')
                                     for k in ('mean', 'std', 'min', 'max', 'p10', 'p90')}
                            upsert_beam_daily(conn, str(d), param, stats,
                                              str(row.get('data_quality', 'ok')))
                elif 'hyper' in name or 'ui' in name:
                    df = parse_hyper_file(path)
                    if not df.empty:
                        insert_events(conn, [
                            (str(r['timestamp']), r['severity'], r['code'],
                             r['function'], r['message'], r['source_file'])
                            for _, r in df.iterrows()
                        ])
                    # Also extract any setlifetime resets so maintenance events are picked
                    # up live without waiting for the next manual ingest run.
                    for evt in extract_from_file(file_path):
                        upsert_maintenance_event(conn, evt['timestamp'], evt['component_key'],
                                                 evt['component_label'], evt['source_file'])
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            _log.warning('Error processing log file %s: %s', name, e)

        # Debounce: skip refresh if one ran recently to avoid pileups during rapid log bursts.
        now = time.monotonic()
        if now - _last_refresh_time >= _MIN_REFRESH_INTERVAL:
            _refresh()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    watcher = LogWatcher(log_dir, on_file=_process_file)
    watcher.start()
    _refresh()
    _log.info('Monitoring %s for new log files. Press Ctrl+C to stop.', log_dir)
    try:
        while True:
            time.sleep(60)
            if not watcher._observer.is_alive():
                _log.error('Log monitor thread has died unexpectedly. Restart the process.')
                break
            # Periodic refresh: catches any file events that were debounced and ensures
            # the dashboard is updated at least every ~2 minutes while the monitor is running.
            if time.monotonic() - _last_refresh_time >= _MIN_REFRESH_INTERVAL:
                _refresh()
    except KeyboardInterrupt:
        pass
    watcher.stop()
