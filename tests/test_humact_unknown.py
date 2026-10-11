"""humact_* features: unknown (NaN) when daily_category is missing or stale,
real counts (0 included) when it is current. A stale table must never read as a
quiet machine, because a model trained on these features could then stand an alarm down."""
import math
import sqlite3
from datetime import date, timedelta

from features.engineer import build_features, HUMACT_CATEGORIES, HUMACT_STALE_DAYS
from tests.conftest import setup_test_db, make_beam_rows

TARGET = date.today()


def _db(tmp_path):
    return setup_test_db(tmp_path, beam_rows=make_beam_rows(TARGET, 40, 'ISC', 100.0))


def _set_categories(db, rows):
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS daily_category "
                "(date TEXT, category TEXT, count INTEGER, PRIMARY KEY(date, category))")
    con.executemany("INSERT INTO daily_category VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def _humact(db):
    f = build_features(TARGET, 'ION SOURCE', db)
    return {k: v for k, v in f.items() if k.startswith('humact_')}


def test_missing_table_is_unknown_not_zero(tmp_path):
    h = _humact(_db(tmp_path))
    assert len(h) == 2 * len(HUMACT_CATEGORIES)
    assert all(isinstance(v, float) and math.isnan(v) for v in h.values())


def test_stale_table_is_unknown_not_zero(tmp_path):
    db = _db(tmp_path)
    stale = (TARGET - timedelta(days=HUMACT_STALE_DAYS + 1)).isoformat()
    _set_categories(db, [(stale, 'struggle', 9)])
    assert all(math.isnan(v) for v in _humact(db).values())


def test_current_table_gives_counts_and_true_zeros(tmp_path):
    db = _db(tmp_path)
    d1 = (TARGET - timedelta(days=1)).isoformat()
    d10 = (TARGET - timedelta(days=10)).isoformat()
    _set_categories(db, [(d1, 'struggle', 4), (d10, 'struggle', 3), (d1, 'reset', 2)])
    h = _humact(db)
    assert h['humact_struggle_7d'] == 4
    assert h['humact_struggle_14d'] == 7
    assert h['humact_reset_7d'] == 2
    assert h['humact_interlock_14d'] == 0
