"""
CLI: python main.py [train|predict|monitor|patterns]
"""
import json
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / 'config.json'


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def cmd_train(cfg):
    from ingest import ingest_all
    from models.trainer import train_component, COMPONENTS
    from features.engineer import build_features

    print("=== Ingesting logs ===")
    stats = ingest_all(cfg['log_dir'], cfg['db_path'])
    print(f"  beam_files={stats['beam_files']}, events={stats['events']}, "
          f"maintenance_events={stats['maintenance_events']}")

    print("=== Training models ===")
    for comp in COMPONENTS:
        ok = train_component(comp, cfg['db_path'], cfg['model_dir'], build_features)
        print(f"  {comp}: {'MODEL' if ok else 'COUNTER-ONLY'}")


def cmd_predict(cfg):
    from datetime import date
    from features.engineer import build_features
    from models.counter import get_counter_days
    from models.predictor import predict
    from monitor.dashboard_writer import write_dashboard
    import sqlite3

    # Use last date in DB so rolling windows have data; fall back to today
    conn = sqlite3.connect(cfg['db_path'])
    row = conn.execute("SELECT MAX(date) FROM beam_daily").fetchone()
    conn.close()
    target_date = date.fromisoformat(row[0]) if row and row[0] else date.today()
    print(f"Predicting as of {target_date} (last log date)")

    COMPONENTS = ['ION SOURCE', 'FOILS', 'BL1 Target 1', 'BL2 Target 1']
    preds = []
    for comp in COMPONENTS:
        feats = build_features(target_date, comp, cfg['db_path'])
        counter_days, _ = get_counter_days(comp, cfg['db_path'])
        conn = sqlite3.connect(cfg['db_path'])
        row = conn.execute(
            "SELECT MAX(date(timestamp)) FROM maintenance_events WHERE component_label=?",
            [comp]
        ).fetchone()
        conn.close()
        last_maint = row[0] if row and row[0] else 'Unknown'
        result = predict(comp, feats, cfg['model_dir'], counter_days, last_maint)
        preds.append(result)
        print(f"  {comp}: {result.alert_level} ({result.days_estimate:.0f}d) [{result.primary_signal}]")

    write_dashboard(preds, cfg['dashboard_path'], cfg['alert_path'])
    print(f"\nDashboard: {cfg['dashboard_path']}")
    print(f"Open ui/index.html in a browser to view.")


def cmd_train_only(cfg):
    """Run only the model training phase (skip ingest — use when DB is already populated)."""
    from models.trainer import train_component, COMPONENTS
    from features.engineer import build_features
    from db import init_db
    init_db(cfg['db_path'])  # ensures indices exist
    print("=== Training models (ingest skipped) ===")
    for comp in COMPONENTS:
        ok = train_component(comp, cfg['db_path'], cfg['model_dir'], build_features)
        print(f"  {comp}: {'MODEL' if ok else 'COUNTER-ONLY'}")


def cmd_monitor(cfg):
    from monitor.watcher import start_monitor
    start_monitor(
        cfg['log_dir'], cfg['db_path'], cfg['model_dir'],
        cfg['dashboard_path'], cfg['alert_path']
    )


def cmd_patterns(cfg):
    from patterns import generate_patterns
    output = str(Path(__file__).parent / 'ui' / 'patterns.html')
    generate_patterns(cfg['db_path'], output)
    print(f"Open ui/patterns.html in a browser to view.")


COMMANDS = {
    'train':      cmd_train,
    'train-only': cmd_train_only,
    'predict':    cmd_predict,
    'monitor':    cmd_monitor,
    'patterns':   cmd_patterns,
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python main.py [{' | '.join(COMMANDS)}]")
        sys.exit(1)
    cfg = load_config()
    COMMANDS[sys.argv[1]](cfg)
