import sqlite3
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from parsers.beam_parser import parse_beam_file, aggregate_daily
from parsers.hyper_parser import parse_hyper_file
from parsers.maintenance_labels import extract_maintenance_events
from db import init_db, upsert_beam_daily, insert_events, upsert_maintenance_event


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
    from models.counter import get_counter_days
    from models.predictor import predict
    from monitor.dashboard_writer import write_dashboard
    from datetime import date as date_type

    init_db(db_path)
    processed = set()
    COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']

    def _refresh():
        preds = []
        for comp in COMPONENTS:
            feats = build_features(date_type.today(), comp, db_path)
            counter_days, _ = get_counter_days(comp, db_path)
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT MAX(date(timestamp)) FROM maintenance_events WHERE component_label=?",
                [comp]
            ).fetchone()
            conn.close()
            last_maint = row[0] if row and row[0] else 'Unknown'
            preds.append(predict(comp, feats, model_dir, counter_days, last_maint))
        write_dashboard(preds, dashboard_path, alert_path)
        print(f"Dashboard updated: {dashboard_path}")

    def _process_file(path):
        if path in processed:
            return
        processed.add(path)
        name = Path(path).name
        try:
            conn = sqlite3.connect(db_path)
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
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"  WARN processing {name}: {e}")
        _refresh()

    watcher = LogWatcher(log_dir, on_file=_process_file)
    watcher.start()
    _refresh()
    print(f"Monitoring {log_dir} for new log files. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    watcher.stop()
