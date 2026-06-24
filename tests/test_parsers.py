import shutil
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from parsers.beam_parser import parse_beam_file, aggregate_daily
from parsers.hyper_parser import parse_hyper_file, extract_lifetime_warnings, extract_valve_toggles
from parsers.maintenance_labels import extract_maintenance_events

FIXTURE = Path(__file__).parent / "fixtures" / "beam_sample.log"
HYPER_FIXTURE = Path(__file__).parent / "fixtures" / "hyper_sample.log"
HYPER_MAINT = Path(__file__).parent / "fixtures" / "hyper_maintenance.log"
HYPER_VALVE = Path(__file__).parent / "fixtures" / "hyper_valve_chattering.log"
HYPER_NEW = Path(__file__).parent / "fixtures" / "hyper_new_format.log"

EXPECTED_PARAMS = [
    'AI_TANK_HI_PRES', 'AI_ISGAS_FLOW', 'SW_RF_FREQ', 'AO_RF_AMPL', 'AI_DEE_VOLT',
    'AI_RFFWD_PWR', 'AI_RFREF_PWR', 'AI_MMA_CUR', 'AI_MMT_CUR', 'AO_MMO_CUR',
    'AI_IS_CUR', 'AI_IS_VOLT', 'AI_BIAS_VOLT', 'AI_BIAS_CUR', 'AI_BL1_FOIL_CUR',
    'AI_BL1_TARG_CUR', 'AI_BL1_COL_CUR', 'AI_BL2_FOIL_CUR', 'AI_BL2_TARG_CUR',
    'AI_BL2_COL_CUR', 'AI_BOP_CUR',
]


def test_beam_parser_returns_22_columns():
    df = parse_beam_file(str(FIXTURE))
    for col in EXPECTED_PARAMS:
        assert col in df.columns, f"Missing: {col}"


def test_beam_parser_handles_date_inheritance():
    df = parse_beam_file(str(FIXTURE))
    assert len(df) == 2
    assert df['timestamp'].dt.date.nunique() == 1


def test_beam_parser_handles_malformed_rows_as_nan():
    df = parse_beam_file(str(FIXTURE))
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2


def test_aggregate_daily_returns_stats():
    df = parse_beam_file(str(FIXTURE))
    daily = aggregate_daily(df)
    assert 'AI_IS_CUR_mean' in daily.columns
    assert 'AI_BOP_CUR_p90' in daily.columns
    assert 'data_quality' in daily.columns
    assert len(daily) >= 1


def test_hyper_parser_extracts_error_codes():
    # hyper_sample.log has no date in filename, so provide date via a temp copy with correct name
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "hyper_260108.log"
        shutil.copy(str(HYPER_FIXTURE), str(dest))
        df = parse_hyper_file(str(dest))
    assert '12072' in df['code'].values
    assert '10804' in df['code'].values


def test_hyper_parser_extracts_lifetime_warnings_with_counter_value():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "hyper_260108.log"
        shutil.copy(str(HYPER_FIXTURE), str(dest))
        df = parse_hyper_file(str(dest))
    warnings = extract_lifetime_warnings(df)
    assert len(warnings) == 1
    assert warnings.iloc[0]['component'] == 'isc_amphrs'
    assert warnings.iloc[0]['counter_uah'] == 10234.0
    assert warnings.iloc[0]['threshold_uah'] == 9999.0


def test_hyper_parser_counts_valve_toggles():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "hyper_260108.log"
        shutil.copy(str(HYPER_VALVE), str(dest))
        df = parse_hyper_file(str(dest))
    toggles = extract_valve_toggles(df, 'DO_BL2_TSU3_VALVE6')
    assert len(toggles) >= 1
    assert toggles['toggle_count'].sum() == 6


def test_maintenance_labels_finds_setlifetime_resets(tmp_path):
    dest = tmp_path / "hyper_260315.log"
    shutil.copy(str(HYPER_MAINT), str(dest))
    df = extract_maintenance_events(str(tmp_path))
    assert len(df) == 2
    assert 'isc_amphrs' in df['component_key'].values
    assert 'bl1_foil1_uamphrs' in df['component_key'].values


def test_maintenance_labels_deduplicates_cmdproc_lines(tmp_path):
    dest = tmp_path / "hyper_260315.log"
    shutil.copy(str(HYPER_MAINT), str(dest))
    df = extract_maintenance_events(str(tmp_path))
    isc_events = df[df['component_key'] == 'isc_amphrs']
    assert len(isc_events) == 1, "cmdProc line must not be double-counted"
