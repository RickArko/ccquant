"""Tests for the lightweight Market Tracker dashboard."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from ccquant.dashboard import (
    build_snapshot_from_panels,
    render_dashboard_html,
)


def _synthetic_daily(
    *,
    n_days: int = 260,
    n_symbols: int = 8,
    end: date | None = None,
) -> pl.DataFrame:
    end = end or date(2026, 7, 18)
    rows: list[dict[str, object]] = []
    symbols = ["BTC", "ETH"] + [f"A{i}" for i in range(n_symbols - 2)]
    for i in range(n_days):
        d = end - timedelta(days=n_days - 1 - i)
        for j, sym in enumerate(symbols):
            # BTC drifts up; half the alts drift down so breadth is mixed/narrow
            base = 50_000.0 if sym == "BTC" else (2_000.0 if sym == "ETH" else 10.0)
            drift = 1.0 + (0.001 if sym == "BTC" else (-0.002 if j % 2 else 0.0005))
            close = base * (drift**i)
            rows.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000.0,
                    "source": "test",
                }
            )
    return pl.DataFrame(rows)


def test_build_snapshot_from_panels_headline_and_stack() -> None:
    daily = _synthetic_daily()
    snap = build_snapshot_from_panels(daily)
    assert snap.as_of == date(2026, 7, 18)
    assert snap.btc_close > 0
    assert snap.headline in {"Risk-on", "Mixed", "Risk-off"}
    assert snap.stack_label in {"Constructive", "Neutral / mixed", "Defensive"}
    assert snap.n_universe >= 2
    assert len(snap.btc_dates) == len(snap.btc_closes) > 10
    assert "Drivers:" in snap.outlook
    assert len(snap.outlook) > 40


def test_build_snapshot_requires_btc() -> None:
    daily = _synthetic_daily().filter(pl.col("symbol") != "BTC")
    with pytest.raises(ValueError, match="BTC"):
        build_snapshot_from_panels(daily)


def test_render_dashboard_html_contains_hero() -> None:
    pytest.importorskip("plotly")
    snap = build_snapshot_from_panels(_synthetic_daily())
    page = render_dashboard_html(snap)
    assert "ccquant" in page
    assert snap.headline in page
    assert "Outlook" in page
    assert "BTC close" in page
    assert "plotly" in page.lower()
