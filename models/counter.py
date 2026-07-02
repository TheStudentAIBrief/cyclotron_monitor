import logging
import re
import sqlite3
from datetime import date, datetime, timedelta

# Fallback constants — used when fewer than 4 maintenance events exist in history.
# dynamic_avg_cycle() supersedes these for components with sufficient event history.
AVG_CYCLES = {'ION SOURCE': 46, 'FOILS': 78, 'BL1 Target 1': 51, 'BL2 Target 1': 56, 'TRANSFER LINES': 35}
# Real vendor warning-message prefixes (events table, code 11001), e.g.
# "ion source Amp-hrs lifetime counter 45 is over 45" — NOT the same as the
# internal setlifetime{} command keys used by parsers/maintenance_labels.py
# (isc_amphrs etc.), which is a different vocabulary for a different purpose.
# Confirmed against production log data for ION SOURCE, all three BL1 foils,
# and BL1 Target 1. BL2 foil/target prefixes below are UNVERIFIED — no BL2
# warning message of this form has ever appeared in ingested logs; extrapolated
# from the BL1 naming convention and should be confirmed once BL2 telemetry exists.
COMPONENT_KEYS = {
    'ION SOURCE': 'ion source Amp-hrs', 'FOILS': 'BL1 foil 1 uAmp-hrs',
    'BL1 Target 1': 'BL1 target 1 uAmp-hrs', 'BL2 Target 1': 'BL2 target 1 uAmp-hrs',
    'TRANSFER LINES': '',  # no µAh counter — calendar-only
}
# All six foil µAh counters — get_counter_days checks each to find the most-worn foil.
FOILS_COUNTER_KEYS = (
    'BL1 foil 1 uAmp-hrs', 'BL1 foil 2 uAmp-hrs', 'BL1 foil 3 uAmp-hrs',
    'BL2 foil 1 uAmp-hrs', 'BL2 foil 2 uAmp-hrs', 'BL2 foil 3 uAmp-hrs',
)
FOILS_LABELS = ('BL1 Foil 1', 'BL1 Foil 2', 'BL1 Foil 3',
                 'BL2 Foil 1', 'BL2 Foil 2', 'BL2 Foil 3')
_COUNTER_RE = re.compile(r'(\w+) lifetime counter ([0-9.]+) is over (\d+)')
# 30-day window averages out short beam-intensity spikes that would otherwise project
# a falsely short lifetime when using a narrower window.
_RATE_WINDOW_DAYS = 30

_log = logging.getLogger('cyclotron.counter')


def dynamic_avg_cycle(conn, component_label: str, cutoff_iso: str) -> int:
    """Return the lower-median inter-maintenance interval from DB history.

    Falls back to AVG_CYCLES when fewer than 4 events exist (gives only 2
    intervals — too few for a stable median; with 2 values the upper-index
    formula returns the max, not the median).  Gaps under 5 days are excluded
    as same-outage re-fixes.  Returns max(fallback, computed) so the dynamic
    value can only correct an under-estimated cycle length upward — it will
    never make a well-calibrated component alarm earlier than its fallback.
    """
    fallback = AVG_CYCLES.get(component_label, 60)
    if component_label == 'FOILS':
        ph = ','.join('?' * len(FOILS_LABELS))
        rows = conn.execute(
            f"SELECT DISTINCT date(timestamp) FROM maintenance_events "
            f"WHERE component_label IN ({ph}) AND date(timestamp) <= ? ORDER BY timestamp",
            list(FOILS_LABELS) + [cutoff_iso]
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT date(timestamp) FROM maintenance_events "
            "WHERE component_label=? AND date(timestamp) <= ? ORDER BY timestamp",
            [component_label, cutoff_iso]
        ).fetchall()
    dates = sorted({date.fromisoformat(r[0]) for r in rows if r[0]})
    if len(dates) < 4:  # need ≥3 intervals for a reliable median
        return fallback
    intervals = sorted(
        (dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)
        if (dates[i + 1] - dates[i]).days >= 5
    )
    if len(intervals) < 3:
        return fallback
    median_val = intervals[(len(intervals) - 1) // 2]  # lower median (not upper)
    return max(fallback, median_val)  # only correct upward — never increase pessimism


def _uah_days_remaining(conn, comp_key: str, window_start: str,
                         window_end: str | None = None) -> float | None:
    """Compute µAh-based days remaining for a single counter key.

    Uses actual elapsed wall-clock time between the first and last warning event
    (not warning-interval count) to compute the daily µAh burn rate.  This is
    correct regardless of how frequently the monitoring system emits 11001 events.

    Returns None when: fewer than 2 readings in window, counter non-monotone
    (mid-run reset suspected), or all timestamp parses fail.

    window_end: caps the event query at this ISO timestamp string.  Pass the
    checkpoint date when computing point-in-time estimates (backtest) to avoid
    seeing future events.  None = no upper bound (production use).
    """
    # `code='11001'` alone never matches real production data — confirmed against
    # data/cyclotron.db that `code` is NULL for every one of these rows; the
    # marker only ever appears as literal text inside the message, e.g.
    # "...lifetime counter 9000 is over 9000 (Warn 11001)". Match on that exact
    # parenthesized marker (not a bare '%11001%', which also matches unrelated
    # numeric noise like "6.11001" elsewhere in the real log data) in addition
    # to the code column, so this works whether a given log format populates
    # code correctly or not.
    if window_end is not None:
        warnings = conn.execute(
            "SELECT timestamp, message FROM events "
            "WHERE (code='11001' OR message LIKE '%(Warn 11001)%') "
            "AND message LIKE ? AND timestamp>=? AND timestamp<? ORDER BY timestamp",
            [f'%{comp_key}%', window_start, window_end]
        ).fetchall()
    else:
        warnings = conn.execute(
            "SELECT timestamp, message FROM events "
            "WHERE (code='11001' OR message LIKE '%(Warn 11001)%') "
            "AND message LIKE ? AND timestamp>=? ORDER BY timestamp",
            [f'%{comp_key}%', window_start]
        ).fetchall()
    if len(warnings) < 2:
        return None

    readings: list[float] = []
    thresholds: list[float] = []
    times: list[datetime] = []
    for ts_str, msg in warnings:
        m = _COUNTER_RE.search(msg)
        if m:
            readings.append(float(m.group(2)))
            thresholds.append(float(m.group(3)))
            try:
                times.append(datetime.fromisoformat(ts_str.replace(' ', 'T')))
            except (ValueError, TypeError):
                pass

    if len(readings) < 2:
        return None

    if readings[-1] < readings[0]:
        _log.warning(
            '%s: µAh counter non-monotone in window (first=%.0f last=%.0f) '
            '— possible mid-run reset without maintenance event; using calendar fallback.',
            comp_key, readings[0], readings[-1]
        )
        return None

    if len(times) == len(readings) and len(times) >= 2:
        elapsed_hours = (times[-1] - times[0]).total_seconds() / 3600
    else:
        # Timestamp parse failed for some readings — fall back to warning-interval count.
        # Less accurate when warning frequency varies; investigate if this fires often.
        _log.debug(
            '%s: timestamp parse failed for %d/%d readings; using interval count as fallback.',
            comp_key, len(readings) - len(times), len(readings)
        )
        elapsed_hours = float(max(1, len(readings) - 1))

    rate_per_hour = (readings[-1] - readings[0]) / max(0.01, elapsed_hours)
    daily_rate = max(0.001, rate_per_hour * 24)
    # Use the threshold the machine itself reported in its most recent warning
    # message, not a hardcoded constant — vendor firmware has been observed to
    # change this value over time (e.g. ION SOURCE moved from 40 to 45 in 2024-11).
    # Clamped to 0 so a counter already past threshold reads "0 days remaining"
    # rather than an unbounded negative number (mirrors the calendar fallback's
    # own clamp below).
    return max(0.0, (thresholds[-1] - readings[-1]) / daily_rate)


def get_counter_days(component_label: str, db_path: str,
                     as_of: date | None = None) -> tuple:
    """Return (days_remaining, days_since_last_maintenance).

    as_of: treat this date as 'today'.  Defaults to the latest date in beam_daily.
    Pass explicitly for point-in-time queries (the walk-forward backtest).

    For FOILS, checks all six individual foil counters and returns the minimum —
    the most-worn foil drives the maintenance decision, not just Foil 1.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        explicit_asof = as_of is not None
        if as_of is None:
            row_d = conn.execute("SELECT MAX(date) FROM beam_daily").fetchone()
            as_of = date.fromisoformat(row_d[0]) if row_d and row_d[0] else date.today()

        cutoff = as_of.isoformat()
        avg_cycle = dynamic_avg_cycle(conn, component_label, cutoff)

        if component_label == 'FOILS':
            ph = ','.join('?' * len(FOILS_LABELS))
            row = conn.execute(
                f"SELECT MAX(date(timestamp)) FROM maintenance_events "
                f"WHERE component_label IN ({ph}) AND date(timestamp) <= ?",
                list(FOILS_LABELS) + [cutoff]
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT MAX(date(timestamp)) FROM maintenance_events "
                "WHERE component_label=? AND date(timestamp) <= ?",
                [component_label, cutoff]
            ).fetchone()
        last_maint = row[0] if row and row[0] else None
        days_since = (as_of - date.fromisoformat(last_maint)).days if last_maint else None

        window_start = (as_of - timedelta(days=_RATE_WINDOW_DAYS)).isoformat()
        # When as_of is explicit (backtest), cap event queries to exclude future events.
        # In production (as_of derived from beam_daily), use no upper bound.
        window_end = cutoff if explicit_asof else None

        if component_label == 'FOILS':
            foil_days = [d for k in FOILS_COUNTER_KEYS
                         if (d := _uah_days_remaining(conn, k, window_start, window_end)) is not None]
            if foil_days:
                return float(min(foil_days)), days_since

        elif COMPONENT_KEYS.get(component_label):
            comp_key = COMPONENT_KEYS[component_label]
            uah = _uah_days_remaining(conn, comp_key, window_start, window_end)
            if uah is not None:
                return float(uah), days_since

        # Calendar fallback: clamped to 0 so a component past its average cycle
        # shows RED (0d) rather than a meaningless negative estimate.
        if days_since is not None:
            return max(0.0, float(avg_cycle - days_since)), days_since
        return float(avg_cycle), None
    finally:
        conn.close()
