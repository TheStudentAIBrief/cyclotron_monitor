"""
TDD tests for monitor/eur_form_parser.py

Written RED before any implementation exists. The EUR form is a photographed
paper Equipment Usage Record sheet for the IBA Cyclone 18/9 cyclotron. Each
sheet has 2-3 operational run entries; each entry yields 4 gauge_readings rows
(Gas Flow, Vacuum, IS Current, Beam on Post).
"""
import json
import pytest
from monitor.eur_form_parser import parse_eur_response, EUR_GAUGES


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resp(entries):
    """Build a JSON string the way Ollama would return it."""
    return json.dumps({'entries': entries})


def _entry(
    date='04/02/2023', operator='JaL',
    gas_flow=6.20, vacuum=1.6e-7, is_current=0.15, beam=268.1,
    comments='OK',
):
    return {
        'date': date, 'operator': operator,
        'gas_flow_sccm': gas_flow, 'vacuum_torr': vacuum,
        'is_current_a': is_current, 'beam_on_post_ua': beam,
        'comments': comments,
    }


def _row(rows, name):
    return next((r for r in rows if r['gauge_name'] == name), None)


# ── Structure ──────────────────────────────────────────────────────────────────

def test_single_entry_produces_four_rows():
    rows = parse_eur_response(_resp([_entry()]))
    assert len(rows) == 4


def test_two_entries_produce_eight_rows():
    rows = parse_eur_response(_resp([_entry(), _entry(date='12/02/2023')]))
    assert len(rows) == 8


def test_four_gauge_types_present():
    rows = parse_eur_response(_resp([_entry()]))
    names = {r['gauge_name'] for r in rows}
    assert names == {'Gas Flow', 'Vacuum', 'IS Current', 'Beam on Post'}


def test_empty_entries_returns_empty_list():
    assert parse_eur_response(_resp([])) == []


def test_invalid_json_returns_empty_list():
    assert parse_eur_response('not json at all') == []


def test_empty_string_returns_empty_list():
    assert parse_eur_response('') == []


def test_missing_entries_key_returns_empty_list():
    assert parse_eur_response('{}') == []


# ── Date parsing ───────────────────────────────────────────────────────────────

def test_sa_date_dd_mm_yyyy_converted_to_iso():
    rows = parse_eur_response(_resp([_entry(date='04/02/2023')]))
    assert all(r['timestamp'].startswith('2023-02-04') for r in rows)


def test_single_digit_day_and_month_normalised():
    rows = parse_eur_response(_resp([_entry(date='4/2/2023')]))
    assert all(r['timestamp'].startswith('2023-02-04') for r in rows)


def test_invalid_date_does_not_raise():
    # Invalid date — rows still produced, timestamp falls back gracefully
    rows = parse_eur_response(_resp([_entry(date='notadate')]))
    assert len(rows) == 4


def test_two_entries_have_different_timestamps():
    rows = parse_eur_response(_resp([_entry(date='04/02/2023'), _entry(date='12/02/2023')]))
    timestamps = {r['timestamp'] for r in rows}
    assert '2023-02-04T00:00:00Z' in timestamps
    assert '2023-02-12T00:00:00Z' in timestamps


# ── Field values ───────────────────────────────────────────────────────────────

def test_gas_flow_unit_is_sccm():
    assert _row(parse_eur_response(_resp([_entry(gas_flow=6.20)])), 'Gas Flow')['unit'] == 'Sccm'


def test_vacuum_unit_is_torr():
    assert _row(parse_eur_response(_resp([_entry()])), 'Vacuum')['unit'] == 'Torr'


def test_is_current_unit_is_amperes():
    assert _row(parse_eur_response(_resp([_entry()])), 'IS Current')['unit'] == 'A'


def test_beam_on_post_unit_is_microamps():
    assert _row(parse_eur_response(_resp([_entry()])), 'Beam on Post')['unit'] == 'µA'


def test_gas_flow_value_stored_correctly():
    r = _row(parse_eur_response(_resp([_entry(gas_flow=6.28)])), 'Gas Flow')
    assert r['value'] == pytest.approx(6.28)


def test_vacuum_scientific_notation_preserved():
    r = _row(parse_eur_response(_resp([_entry(vacuum=1.6e-7)])), 'Vacuum')
    assert r['value'] == pytest.approx(1.6e-7)


def test_operator_stored_as_verified_by():
    rows = parse_eur_response(_resp([_entry(operator='JaL')]))
    assert all(r['verified_by'] == 'JaL' for r in rows)


def test_location_is_control_room():
    rows = parse_eur_response(_resp([_entry()]))
    assert all(r['location'] == 'Control Room' for r in rows)


def test_confidence_is_eur_form():
    rows = parse_eur_response(_resp([_entry()]))
    assert all(r['confidence'] == 'eur_form' for r in rows)


# ── Alert / action thresholds ─────────────────────────────────────────────────

def test_vacuum_normal():
    r = _row(parse_eur_response(_resp([_entry(vacuum=1.6e-7)])), 'Vacuum')
    assert r['is_alert'] == 0
    assert r['alert_reason'] == ''


def test_vacuum_alert_when_above_5e_minus7():
    r = _row(parse_eur_response(_resp([_entry(vacuum=8e-7)])), 'Vacuum')
    assert r['is_alert'] == 1
    assert r['alert_reason'] == 'ALERT'


def test_vacuum_action_when_above_1e_minus6():
    r = _row(parse_eur_response(_resp([_entry(vacuum=2e-6)])), 'Vacuum')
    assert r['is_alert'] == 1
    assert r['alert_reason'] == 'ACTION'


def test_gas_flow_alert_when_below_5_5():
    r = _row(parse_eur_response(_resp([_entry(gas_flow=5.0)])), 'Gas Flow')
    assert r['is_alert'] == 1


def test_gas_flow_action_when_below_5_0():
    r = _row(parse_eur_response(_resp([_entry(gas_flow=4.5)])), 'Gas Flow')
    assert r['alert_reason'] == 'ACTION'


def test_gas_flow_normal():
    r = _row(parse_eur_response(_resp([_entry(gas_flow=6.2)])), 'Gas Flow')
    assert r['is_alert'] == 0


def test_beam_action_when_below_30():
    r = _row(parse_eur_response(_resp([_entry(beam=20.0)])), 'Beam on Post')
    assert r['is_alert'] == 1
    assert r['alert_reason'] == 'ACTION'


def test_beam_alert_when_below_50():
    r = _row(parse_eur_response(_resp([_entry(beam=40.0)])), 'Beam on Post')
    assert r['is_alert'] == 1
    assert r['alert_reason'] == 'ALERT'


def test_beam_normal():
    r = _row(parse_eur_response(_resp([_entry(beam=268.1)])), 'Beam on Post')
    assert r['is_alert'] == 0


# ── None/missing field handling ────────────────────────────────────────────────

def test_none_gas_flow_skipped():
    entry = _entry()
    entry['gas_flow_sccm'] = None
    rows = parse_eur_response(_resp([entry]))
    assert 'Gas Flow' not in {r['gauge_name'] for r in rows}
    assert len(rows) == 3


def test_missing_gas_flow_key_skipped():
    entry = {k: v for k, v in _entry().items() if k != 'gas_flow_sccm'}
    rows = parse_eur_response(_resp([entry]))
    assert len(rows) == 3


def test_all_fields_none_produces_empty():
    entry = {'date': '04/02/2023', 'operator': 'JaL',
             'gas_flow_sccm': None, 'vacuum_torr': None,
             'is_current_a': None, 'beam_on_post_ua': None}
    rows = parse_eur_response(_resp([entry]))
    assert rows == []


def test_threshold_fields_present_in_row():
    r = _row(parse_eur_response(_resp([_entry()])), 'Vacuum')
    assert 'alert_lo' in r
    assert 'alert_hi' in r
    assert 'action_lo' in r
    assert 'action_hi' in r
