import sqlite3
import pytest
from datetime import date, timedelta
from models.counter import get_counter_days
from models.predictor import predict, PredictionResult
from tests.conftest import setup_test_db, make_beam_rows


def _insert_maint(tmp_path, component_label, days_ago):
    maint_date = (date.today() - timedelta(days=days_ago)).isoformat()
    maint_rows = [(f"{maint_date} 10:00:00", 'isc_amphrs', component_label, 'hyper.log')]
    return setup_test_db(tmp_path, maint_rows=maint_rows)


def test_counter_uses_historical_when_no_warnings(tmp_path):
    db = _insert_maint(tmp_path, 'ION SOURCE', 20)
    days, since = get_counter_days('ION SOURCE', db)
    assert since == 20
    assert abs(days - (46 - 20)) < 1.0


def test_counter_returns_positive_avg_when_no_history(tmp_path):
    db = setup_test_db(tmp_path)
    days, since = get_counter_days('ION SOURCE', db)
    assert days == 46.0
    assert since is None


def _features_stub():
    return {'AI_IS_CUR_7d_mean': 2.0, 'days_since_last_maintenance': 20.0,
            'counter_days_remaining': 26.0, 'post_v51_software': 0}


def test_predictor_returns_red_when_counter_at_zero(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 0.0, '2025-01-01')
    assert result.alert_level == 'RED'


def test_predictor_returns_green_when_14_plus_days(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 28.0, '2025-01-01')
    assert result.alert_level == 'GREEN'


def test_predictor_uses_counter_only_when_no_model(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 5.0, '2025-01-01')
    assert result.primary_signal == 'COUNTER_ONLY'


def test_predictor_plain_english_reasons_contain_no_jargon(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 30.0, '2025-01-01')
    for reason in result.top_reasons:
        assert 'gradient' not in reason.lower(), f"ML jargon in reason: {reason}"
        assert 'gbm' not in reason.lower(), f"ML jargon in reason: {reason}"


def test_predictor_risk_score_between_0_and_1(tmp_path):
    result = predict('ION SOURCE', _features_stub(), str(tmp_path), 10.0, '2025-01-01')
    assert 0.0 <= result.risk_score <= 1.0


def _build_synthetic_db(tmp_path, n_cycles=4, cycle_len=46):
    # Include all 5 IS params so features have <30% NaN and rows aren't filtered out
    IS_PARAMS = ['AI_IS_CUR', 'AI_IS_VOLT', 'AI_BIAS_VOLT', 'AI_BIAS_CUR', 'AI_BOP_CUR']
    start = date(2024, 10, 1)
    beam_rows, maint_rows = [], []
    for cycle in range(n_cycles):
        maint_date = start + timedelta(days=(cycle + 1) * cycle_len)
        for d_offset in range(cycle_len):
            d = start + timedelta(days=cycle * cycle_len + d_offset)
            is_val = 2.0 if d_offset < cycle_len - 14 else 2.0 - 0.1 * (d_offset - (cycle_len - 14))
            for param in IS_PARAMS:
                val = max(0.1, is_val) if param == 'AI_IS_CUR' else 2.0
                beam_rows += make_beam_rows(d + timedelta(days=1), 1, param, val)
        maint_rows.append((f"{maint_date.isoformat()} 10:00:00",
                           'isc_amphrs', 'ION SOURCE', 'hyper.log'))
    return setup_test_db(tmp_path, beam_rows=beam_rows, maint_rows=maint_rows)


def test_build_training_data_labels_correctly(tmp_path):
    from models.trainer import build_training_data
    from features.engineer import build_features
    db = _build_synthetic_db(tmp_path)
    result = build_training_data('ION SOURCE', db, build_features)
    if result[0] is None:
        pytest.skip("Not enough training data in synthetic DB")
    X, y, days_arr, feature_names, dates = result
    for d, label, days in zip(dates, y, days_arr):
        if label == 1:
            assert days <= 7, f"Positive label {days} days before maintenance (>7)"
        if label == 0:
            assert days > 14, f"Negative label {days} days before maintenance (<=14)"
