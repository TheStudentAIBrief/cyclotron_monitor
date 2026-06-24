import json
import time
from pathlib import Path
from models.predictor import PredictionResult
from monitor.dashboard_writer import write_dashboard
from monitor.watcher import LogWatcher


def _make_pred(component, days, alert_level):
    return PredictionResult(
        component=component, risk_score=0.5, days_estimate=days,
        alert_level=alert_level, primary_signal='COUNTER_ONLY',
        top_reasons=["Test reason"], last_maintenance='2025-01-01',
        counter_days=days,
    )


def test_dashboard_json_schema_matches_spec(tmp_path):
    preds = [_make_pred('ION SOURCE', 20.0, 'GREEN')]
    dash = str(tmp_path / 'dashboard.json')
    write_dashboard(preds, dash, str(tmp_path / 'ALERT.txt'))
    with open(dash) as f:
        data = json.load(f)
    assert 'generated_at' in data
    assert 'components' in data
    comp = data['components'][0]
    for key in ('name', 'risk_score', 'days_estimate', 'alert_level',
                'last_maintenance', 'top_reasons', 'primary_signal'):
        assert key in comp, f"Missing key: {key}"


def test_alert_txt_written_on_red_component(tmp_path):
    preds = [_make_pred('ION SOURCE', 1.0, 'RED')]
    alert = str(tmp_path / 'ALERT.txt')
    write_dashboard(preds, str(tmp_path / 'd.json'), alert)
    assert Path(alert).exists(), "ALERT.txt should exist when RED"
    content = Path(alert).read_text()
    assert 'ION SOURCE' in content


def test_alert_txt_not_written_when_all_green(tmp_path):
    preds = [_make_pred('ION SOURCE', 30.0, 'GREEN')]
    alert = str(tmp_path / 'ALERT.txt')
    write_dashboard(preds, str(tmp_path / 'd.json'), alert)
    assert not Path(alert).exists(), "ALERT.txt must not exist when all GREEN"


def test_watcher_detects_new_log_file(tmp_path):
    detected = []
    watcher = LogWatcher(str(tmp_path), on_file=lambda p: detected.append(p))
    watcher.start()
    time.sleep(0.2)
    (tmp_path / "beam_260624.log").write_text("test")
    time.sleep(0.5)
    watcher.stop()
    assert any('beam_260624' in p for p in detected), \
        f"Watcher did not detect new file. Detected: {detected}"
