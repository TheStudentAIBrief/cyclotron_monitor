from pathlib import Path
import pandas as pd
from parsers.batch_beam_parser import is_batch_beam_file, parse_batch_beam_file
from parsers.beam_parser import aggregate_daily

SAMPLE = Path(__file__).parent / "fixtures" / "batch_beam_sample.log"
EMPTY = Path(__file__).parent / "fixtures" / "batch_beam_empty.log"


def test_is_batch_beam_file_detects_sample():
    assert is_batch_beam_file(str(SAMPLE)) is True


def test_is_batch_beam_file_rejects_non_matching_file(tmp_path):
    other = tmp_path / "not_a_batch_log.log"
    other.write_text("DATE,TIME,foo\n01/01/2024,00:00:00,1\n")
    assert is_batch_beam_file(str(other)) is False


def test_parse_batch_beam_file_returns_expected_columns():
    df = parse_batch_beam_file(str(SAMPLE))
    for col in ['Arc-I', 'Arc-V', 'Magnet-I', 'RF fwd-W', 'Foil No']:
        assert col in df.columns, f"Missing: {col}"
    assert 'timestamp' in df.columns


def test_parse_batch_beam_file_combines_header_date_with_row_time():
    df = parse_batch_beam_file(str(SAMPLE))
    assert len(df) == 4
    assert df['timestamp'].dt.date.nunique() == 1
    assert str(df['timestamp'].dt.date.iloc[0]) == '2024-06-16'
    assert df['timestamp'].iloc[0].strftime('%H:%M:%S') == '17:00:23'


def test_parse_batch_beam_file_handles_irregular_date_spacing():
    df = parse_batch_beam_file(str(EMPTY))
    assert isinstance(df, pd.DataFrame)


def test_parse_batch_beam_file_empty_batch_returns_empty_dataframe():
    df = parse_batch_beam_file(str(EMPTY))
    assert df.empty


def test_aggregate_daily_works_on_batch_beam_output():
    df = parse_batch_beam_file(str(SAMPLE))
    daily = aggregate_daily(df)
    assert 'Arc-V_mean' in daily.columns
    assert 'data_quality' in daily.columns
    assert len(daily) == 1
