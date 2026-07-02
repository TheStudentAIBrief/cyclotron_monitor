from datetime import date
from models.counter import get_counter_days
from tests.conftest import setup_test_db


def test_ion_source_counter_matches_real_warning_message_format(tmp_path):
    # Real production message format (confirmed against data/cyclotron.db):
    # "ion source Amp-hrs lifetime counter <value> is over <threshold>"
    event_rows = [
        ('2025-07-01 00:00:00', 'warning', '11001', 'func',
         'ion source Amp-hrs lifetime counter 40.0 is over 45', 'hyper.log'),
        ('2025-07-05 00:00:00', 'warning', '11001', 'func',
         'ion source Amp-hrs lifetime counter 42.0 is over 45', 'hyper.log'),
    ]
    db = setup_test_db(tmp_path, event_rows=event_rows)
    days, since = get_counter_days('ION SOURCE', db, as_of=date(2025, 7, 10))
    # 2.0 units over 4 days = 0.5 units/day; (45 - 42) / 0.5 = 6.0 days remaining.
    # Before the fix, the message never matches COMPONENT_KEYS['ION SOURCE']
    # ('isc_amphrs') and this falls back to the calendar estimate (46.0), so
    # this assertion distinguishes fixed vs. broken behavior unambiguously.
    assert days == 6.0


def test_foils_counter_matches_real_warning_message_format(tmp_path):
    event_rows = [
        ('2025-07-01 00:00:00', 'warning', '11001', 'func',
         'BL1 foil 1 uAmp-hrs lifetime counter 9000.0 is over 9999', 'hyper.log'),
        ('2025-07-05 00:00:00', 'warning', '11001', 'func',
         'BL1 foil 1 uAmp-hrs lifetime counter 9400.0 is over 9999', 'hyper.log'),
    ]
    db = setup_test_db(tmp_path, event_rows=event_rows)
    days, since = get_counter_days('FOILS', db, as_of=date(2025, 7, 10))
    # 400 units over 4 days = 100 units/day; (9999 - 9400) / 100 = 5.99 days.
    assert abs(days - 5.99) < 0.01


def test_ion_source_counter_uses_latest_reported_threshold_not_hardcoded(tmp_path):
    # Simulates a real observed firmware behavior: the threshold itself changed
    # between messages (40 -> 45). The correct days-remaining must be computed
    # against the MOST RECENT threshold the machine reported (45), not an old
    # value, and not any hardcoded constant.
    event_rows = [
        ('2025-07-01 00:00:00', 'warning', '11001', 'func',
         'ion source Amp-hrs lifetime counter 40.0 is over 40', 'hyper.log'),
        ('2025-07-05 00:00:00', 'warning', '11001', 'func',
         'ion source Amp-hrs lifetime counter 42.0 is over 45', 'hyper.log'),
    ]
    db = setup_test_db(tmp_path, event_rows=event_rows)
    days, since = get_counter_days('ION SOURCE', db, as_of=date(2025, 7, 10))
    # rate = (42-40)/4d = 0.5/day; (45 - 42) / 0.5 = 6.0 using the LATEST
    # threshold (45). Using the old hardcoded 9999.0 would give ~19914 days;
    # using the stale first-seen threshold (40) would give a negative number.
    assert days == 6.0


def test_counter_matches_real_production_data_shape_null_code(tmp_path):
    # Regression: confirmed against the real local database (data/cyclotron.db)
    # that `code` is NULL for every one of these warning rows in practice - the
    # code only ever appears as literal text inside the message, e.g.
    # "checkLifetime: BL1 target 1 uAmp-hrs lifetime counter 9000 is over 9000
    # (Warn 11001)". The old `WHERE code='11001'` query never matched a single
    # real row, so the production µA·h counter has likely never actually fired
    # against real data - it silently fell back to the calendar estimate every
    # time, for every component, without ever surfacing an error.
    event_rows = [
        ('2025-07-01 00:00:00', 'warning', None, 'checkLifetime',
         'checkLifetime: BL1 target 1 uAmp-hrs lifetime counter 9000 is over 9000 (Warn 11001)',
         'hyper.log'),
        ('2025-07-05 00:00:00', 'warning', None, 'checkLifetime',
         'checkLifetime: BL1 target 1 uAmp-hrs lifetime counter 9400 is over 9000 (Warn 11001)',
         'hyper.log'),
    ]
    db = setup_test_db(tmp_path, event_rows=event_rows)
    days, since = get_counter_days('BL1 Target 1', db, as_of=date(2025, 7, 10))
    # 400 units over 4 days = 100 units/day; already past threshold (9400 > 9000) -> clamped to 0.
    # Before the fix: code='11001' matches nothing, falls back to calendar
    # (AVG_CYCLES['BL1 Target 1']=51, no maintenance_events -> returns 51.0, not 0.0).
    assert days == 0.0


def test_counter_does_not_false_positive_on_numbers_containing_11001(tmp_path):
    # Regression: a bare `message LIKE '%11001%'` (the naive fix) would also
    # match unrelated numeric text that happens to contain the substring
    # "11001", e.g. a real value like "6.11001" from an unrelated log line -
    # confirmed this exists in the real database (fastSplitEstimate lines).
    # The fix must match the specific "(Warn 11001)" marker, not a bare substring.
    event_rows = [
        ('2025-07-01 00:00:00', 'debug', None, 'fastSplitEstimate',
         'fastSplitEstimate: 2.23, 3.27752, 4.23593, 3.94642, 2.86835, 1.98866, 7.10428, 5.93507, 6.11001',
         'hyper.log'),
        ('2025-07-05 00:00:00', 'debug', None, 'fastSplitEstimate',
         'fastSplitEstimate: 2.27, 3.52007, 2.63461, 2.38486, 1.5912, 0.903185, 4.22581, 3.28805, 1.11001',
         'hyper.log'),
    ]
    db = setup_test_db(tmp_path, event_rows=event_rows)
    days, since = get_counter_days('BL1 Target 1', db, as_of=date(2025, 7, 10))
    # No real "(Warn 11001)" warning present -> falls back to calendar (no
    # maintenance_events -> AVG_CYCLES['BL1 Target 1'] = 51.0), NOT a bogus
    # µA·h estimate parsed from unrelated numeric noise.
    assert days == 51.0


def test_ion_source_counter_clamps_at_zero_when_already_past_threshold(tmp_path):
    # Counter flat at 50, already past the reported threshold of 45 (matches a
    # real-world "onboard system flags RED / zero life left" state). Without a
    # clamp this divides by the daily_rate floor and returns a huge negative
    # number (e.g. -5000.0) instead of the correct "0 days remaining, overdue".
    event_rows = [
        ('2025-07-01 00:00:00', 'warning', '11001', 'func',
         'ion source Amp-hrs lifetime counter 50.0 is over 45', 'hyper.log'),
        ('2025-07-05 00:00:00', 'warning', '11001', 'func',
         'ion source Amp-hrs lifetime counter 50.0 is over 45', 'hyper.log'),
    ]
    db = setup_test_db(tmp_path, event_rows=event_rows)
    days, since = get_counter_days('ION SOURCE', db, as_of=date(2025, 7, 10))
    assert days == 0.0
