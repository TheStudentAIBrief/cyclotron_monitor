"""
TDD tests for the PETrace log parser.
All tests written FIRST (RED), implementation comes after.

PETrace 800 log format:
  Line 1: "Tracer: (N) Name\t\t\tBatch no: N\t\t\tDate: YYYY-MM-DD"
  Line 2: "Site name: geps"
  Line 3: blank
  Line 4: column header (26 tab-separated names)
  Lines 5+: data rows (tab-separated values, ~3-second intervals)
"""
import pytest
from monitor.petrace_parser import parse_log, parse_header, parse_rows, summarise

# ── Minimal single-row fixture ────────────────────────────────────────────────

HEADER_STANDARD = (
    "Tracer: (1) Dummy P w/o He-cooling\t\t\tBatch no: 1\t\t\tDate: 2024-06-16\n"
    "Site name: geps\n"
    "\n"
)

COL_HEADER = (
    "Time\tArc-I\tArc-V\tGas flow\tDee-1-kV\tDee-2-kV\tMagnet-I\t"
    "Foil-I\tColl-l-I\tTarget-I\tColl-r-I\tVacuum-P\tTarget-P\t"
    "Delta Dee-kV\tPhase load\tDee ref-V\tProbe-I\tHe cool-P\t"
    "Flap1-pos\tFlap2-pos\tStep pos\tExtr pos\tBalance\tRF fwd-W\tRF refl-W\tFoil No\n"
)

# Two data rows, ~3 s apart
ROW_A = "17:00:23 \t38\t1312\t5.0\t36.9\t40.1\t426.6\t24.7\t2.3\t20.5\t1.6\t1.15E-05\t0.0\t3.1\t7.7\t37.0\t14.4\t0.0\t44.9\t22.5\t0.0\t35.1\t27.3\t11.1\t0.0\t1\n"
ROW_B = "17:00:26 \t36\t1330\t5.0\t37.0\t40.0\t426.6\t10.9\t0.8\t9.2\t0.9\t1.15E-05\t0.0\t3.0\t5.4\t37.0\t0.8\t0.6\t45.8\t22.5\t0.0\t31.8\t27.3\t11.4\t0.0\t1\n"

ONE_ROW_LOG = HEADER_STANDARD + COL_HEADER + ROW_A
TWO_ROW_LOG = HEADER_STANDARD + COL_HEADER + ROW_A + ROW_B

# Log with only header — no data rows (mirrors real batch 83)
EMPTY_LOG = (
    "Tracer: (4) 18F- self-shielded\t\t\tBatch no: 83\t\t\tDate: 2025-02- 5\n"
    "Site name: geps\n"
    "\n"
    + COL_HEADER
)

# ── Header parsing ─────────────────────────────────────────────────────────────

def test_parse_header_batch_no():
    h = parse_header(HEADER_STANDARD)
    assert h['batch_no'] == 1

def test_parse_header_date():
    h = parse_header(HEADER_STANDARD)
    assert h['batch_date'] == '2024-06-16'

def test_parse_header_tracer_name():
    h = parse_header(HEADER_STANDARD)
    assert h['tracer_name'] == 'Dummy P w/o He-cooling'

def test_parse_header_tracer_num():
    h = parse_header(HEADER_STANDARD)
    assert h['tracer_num'] == 1

def test_parse_header_site():
    h = parse_header(HEADER_STANDARD)
    assert h['site'] == 'geps'

def test_parse_header_spaced_date():
    """'2025-02- 5' must be normalised to '2025-02-05'."""
    h = parse_header(EMPTY_LOG)
    assert h['batch_date'] == '2025-02-05'

# ── Row parsing ───────────────────────────────────────────────────────────────

def test_parse_rows_count_two():
    rows = parse_rows(TWO_ROW_LOG)
    assert len(rows) == 2

def test_parse_rows_count_empty():
    rows = parse_rows(EMPTY_LOG)
    assert len(rows) == 0

def test_parse_rows_target_I():
    rows = parse_rows(ONE_ROW_LOG)
    assert rows[0]['target_I'] == pytest.approx(20.5)

def test_parse_rows_arc_I():
    rows = parse_rows(ONE_ROW_LOG)
    assert rows[0]['arc_I'] == pytest.approx(38.0)

def test_parse_rows_vacuum_scientific_notation():
    """Vacuum-P is stored in scientific notation e.g. 1.15E-05."""
    rows = parse_rows(ONE_ROW_LOG)
    assert rows[0]['vacuum_P'] == pytest.approx(1.15e-05)

def test_parse_rows_foil_no_integer():
    rows = parse_rows(ONE_ROW_LOG)
    assert rows[0]['foil_no'] == 1
    assert isinstance(rows[0]['foil_no'], int)

def test_parse_rows_time_string():
    rows = parse_rows(ONE_ROW_LOG)
    assert rows[0]['time'] == '17:00:23'

def test_parse_rows_rf_fwd():
    rows = parse_rows(ONE_ROW_LOG)
    assert rows[0]['rf_fwd_W'] == pytest.approx(11.1)

# ── Summary stats ─────────────────────────────────────────────────────────────

def test_summarise_peak_target_uA():
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    assert s['peak_target_uA'] == pytest.approx(20.5)

def test_summarise_avg_target_uA():
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    # avg of 20.5 and 9.2
    assert s['avg_target_uA'] == pytest.approx((20.5 + 9.2) / 2)

def test_summarise_total_muAh():
    """17:00:23 → 17:00:26 = 3 seconds.
    µAh = avg_target_I (µA) × Δt (h)
    = ((20.5 + 9.2) / 2) × (3 / 3600) ≈ 0.01238 µAh."""
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    expected = ((20.5 + 9.2) / 2) * (3 / 3600)
    assert s['total_muAh'] == pytest.approx(expected, rel=0.01)

def test_summarise_duration_s():
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    assert s['duration_s'] == pytest.approx(3.0)

def test_summarise_foil_no_last_row():
    """foil_no = foil number from the last data row."""
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    assert s['foil_no'] == 1

def test_summarise_rf_efficiency():
    """RF efficiency = mean((fwd - refl) / fwd). ROW_A: (11.1-0.0)/11.1=1.0, ROW_B: (11.4-0.0)/11.4=1.0."""
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    assert s['rf_efficiency'] == pytest.approx(1.0, rel=0.01)

def test_summarise_avg_arc_I():
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    assert s['avg_arc_I'] == pytest.approx((38.0 + 36.0) / 2)

def test_summarise_peak_vacuum_P():
    rows = parse_rows(TWO_ROW_LOG)
    s = summarise(rows)
    assert s['peak_vacuum_P'] == pytest.approx(1.15e-05)

def test_summarise_empty_returns_zeros():
    rows = parse_rows(EMPTY_LOG)
    s = summarise(rows)
    assert s['peak_target_uA'] == 0.0
    assert s['total_muAh'] == 0.0
    assert s['duration_s'] == 0.0
    assert s['foil_no'] is None

# ── Full parse_log integration ────────────────────────────────────────────────

def test_parse_log_combines_header_and_summary():
    result = parse_log(TWO_ROW_LOG)
    assert result['batch_no'] == 1
    assert result['batch_date'] == '2024-06-16'
    assert result['row_count'] == 2
    assert result['peak_target_uA'] == pytest.approx(20.5)

def test_parse_log_empty_batch():
    result = parse_log(EMPTY_LOG)
    assert result['batch_no'] == 83
    assert result['batch_date'] == '2025-02-05'
    assert result['row_count'] == 0
    assert result['peak_target_uA'] == 0.0
