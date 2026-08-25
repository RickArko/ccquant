"""Calendar-spine integrity for crypto daily OHLCV.

Crypto trades every UTC day. A missing date between the first and last
stored bar is a hole, not a holiday. ``latest_at = max(date)`` does not
imply a contiguous series.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

DAILY_HOLE_LOOKBACK_DAYS = 90


def as_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def calendar_holes(
    dates: Sequence[date],
    *,
    start: date,
    end: date,
) -> tuple[date, ...]:
    """UTC calendar days in ``[start, end]`` absent from ``dates``."""
    if end < start:
        return ()
    have = set(dates)
    missing: list[date] = []
    day = start
    one = timedelta(days=1)
    while day <= end:
        if day not in have:
            missing.append(day)
        day += one
    return tuple(missing)


def interior_calendar_holes(
    dates: Sequence[date],
    *,
    start: date,
    end: date,
) -> tuple[date, ...]:
    """Holes up to the last stored date (trailing open day is not a hole)."""
    if not dates:
        return calendar_holes((), start=start, end=end)
    first = min(dates)
    last = max(dates)
    lo = max(start, first)
    cap = min(end, last)
    return calendar_holes(dates, start=lo, end=cap)


def daily_tail_start(
    *,
    today: date,
    tail_days: int,
    latest_at: date | datetime | None,
    stored_dates: Sequence[date],
    hole_lookback_days: int = DAILY_HOLE_LOOKBACK_DAYS,
) -> date:
    """First date a completed-backfill tail should re-fetch.

    Happy path is ``today - tail_days``. Interior holes in the lookback
    window rewind the start so a skipped week cannot hide behind
    ``max(date)``.
    """
    start = today - timedelta(days=max(tail_days, 0))
    latest = as_date(latest_at)
    if latest is not None:
        start = min(start, latest)
    if stored_dates:
        window = today - timedelta(days=max(hole_lookback_days, tail_days, 0))
        holes = interior_calendar_holes(stored_dates, start=window, end=today)
        if holes:
            start = min(start, holes[0])
    return start


@dataclass(frozen=True)
class DailyCoverage:
    symbol: str
    first: date | None
    last: date | None
    n_rows: int
    n_holes: int
    first_hole: date | None
    last_hole: date | None
    contiguous_through: date | None
    holes: tuple[date, ...]


def daily_coverage(
    symbol: str,
    dates: Sequence[date],
    *,
    start: date | None = None,
    end: date | None = None,
) -> DailyCoverage:
    if not dates:
        return DailyCoverage(
            symbol=symbol.upper(),
            first=None,
            last=None,
            n_rows=0,
            n_holes=0,
            first_hole=None,
            last_hole=None,
            contiguous_through=None,
            holes=(),
        )
    first = min(dates)
    last = max(dates)
    lo = start if start is not None else first
    hi = end if end is not None else last
    holes = interior_calendar_holes(dates, start=lo, end=hi)
    contiguous: date | None
    if holes:
        candidate = holes[0] - timedelta(days=1)
        contiguous = candidate if candidate >= first else None
    else:
        contiguous = last
    return DailyCoverage(
        symbol=symbol.upper(),
        first=first,
        last=last,
        n_rows=len(set(dates)),
        n_holes=len(holes),
        first_hole=holes[0] if holes else None,
        last_hole=holes[-1] if holes else None,
        contiguous_through=contiguous,
        holes=holes,
    )
