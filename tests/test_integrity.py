"""Calendar-spine integrity for crypto daily series."""

from __future__ import annotations

from datetime import date, timedelta

from ccquant.integrity import (
    calendar_holes,
    daily_coverage,
    daily_tail_start,
    interior_calendar_holes,
)


def test_calendar_holes_july_2026_week() -> None:
    dates = [date(2026, 7, 18), date(2026, 7, 26)]
    holes = calendar_holes(dates, start=date(2026, 7, 18), end=date(2026, 7, 26))
    assert holes == tuple(date(2026, 7, d) for d in range(19, 26))


def test_interior_holes_ignore_trailing_open_day() -> None:
    dates = [date(2026, 7, d) for d in range(1, 26)]
    holes = interior_calendar_holes(
        dates, start=date(2026, 7, 1), end=date(2026, 8, 24)
    )
    assert holes == ()


def test_daily_tail_start_rewinds_to_first_hole() -> None:
    today = date(2026, 8, 24)
    stored: list[date] = []
    day = date(2026, 6, 1)
    while day <= today:
        if not (date(2026, 7, 19) <= day <= date(2026, 7, 25)):
            stored.append(day)
        day += timedelta(days=1)
    start = daily_tail_start(
        today=today,
        tail_days=7,
        latest_at=date(2026, 8, 24),
        stored_dates=stored,
        hole_lookback_days=90,
    )
    assert start == date(2026, 7, 19)


def test_daily_tail_start_happy_path_unchanged() -> None:
    today = date(2026, 8, 24)
    stored = [
        today - timedelta(days=i) for i in range(40, -1, -1)
    ]
    start = daily_tail_start(
        today=today,
        tail_days=7,
        latest_at=today,
        stored_dates=stored,
        hole_lookback_days=90,
    )
    assert start == date(2026, 8, 17)


def test_daily_coverage_july_sandwich() -> None:
    dates = [date(2026, 7, 18), date(2026, 7, 26)]
    cov = daily_coverage("BTC", dates)
    assert cov.n_holes == 7
    assert cov.first_hole == date(2026, 7, 19)
    assert cov.last_hole == date(2026, 7, 25)
    assert cov.contiguous_through == date(2026, 7, 18)
