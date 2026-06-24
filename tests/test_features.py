import numpy as np
import pytest
from datetime import date, timedelta
from features.engineer import build_features
from tests.conftest import make_beam_rows, setup_test_db


def test_engineer_computes_rolling_slope_correctly(tmp_path):
    target = date(2025, 3, 15)
    beam = make_beam_rows(target, 14, 'AI_IS_CUR', 2.0, slope=0.1)
    db = setup_test_db(tmp_path, beam_rows=beam)
    f = build_features(target, 'ION SOURCE', db)
    slope = f.get('AI_IS_CUR_14d_slope', np.nan)
    assert not np.isnan(slope), "Slope must not be NaN with 14 days of data"
    assert abs(slope - 0.1) < 0.05, f"Expected slope ~0.1, got {slope}"


def test_engineer_returns_nan_when_fewer_than_7_days(tmp_path):
    target = date(2025, 3, 15)
    beam = make_beam_rows(target, 3, 'AI_IS_CUR', 2.0)
    db = setup_test_db(tmp_path, beam_rows=beam)
    f = build_features(target, 'ION SOURCE', db)
    assert np.isnan(f.get('AI_IS_CUR_7d_mean', np.nan)), "Must be NaN with only 3 days"


def test_engineer_computes_efficiency_ratio(tmp_path):
    target = date(2025, 3, 15)
    beam = (make_beam_rows(target, 14, 'AI_IS_CUR', 4.0) +
            make_beam_rows(target, 14, 'AI_BOP_CUR', 8.0))
    db = setup_test_db(tmp_path, beam_rows=beam)
    f = build_features(target, 'ION SOURCE', db)
    ratio = f.get('efficiency_ratio', np.nan)
    assert not np.isnan(ratio)
    assert abs(ratio - 2.0) < 0.01, f"Expected BOP/IS = 8/4 = 2.0, got {ratio}"


def test_engineer_computes_fault_rates(tmp_path):
    target = date(2025, 3, 15)
    events = [
        (f'2025-03-{9+i:02d} 10:00:00', 'warning', '10802',
         'periodicCheckISC', 'IS check failed', 'hyper.log')
        for i in range(3)
    ]
    db = setup_test_db(tmp_path, event_rows=events)
    f = build_features(target, 'ION SOURCE', db)
    assert f.get('fault_is_10802_7d', 0) == 3


def test_engineer_computes_valve_toggle_rate(tmp_path):
    target = date(2026, 1, 8)
    events = [
        (f'2026-01-0{(i % 7) + 1} 04:{40 + i % 10:02d}:00', 'info', None,
         'archSync', 'IO Channel DO_BL2_TSU3_VALVE6 set to ON', 'hyper.log')
        for i in range(22)
    ]
    db = setup_test_db(tmp_path, event_rows=events)
    f = build_features(target, 'BL2 Target 1', db)
    assert f.get('valve_bl2_tsu3_toggles_7d', 0) == 22
