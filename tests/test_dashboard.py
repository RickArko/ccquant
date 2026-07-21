"""Tests for the lightweight Market Tracker dashboard."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from ccquant.dashboard import (
    build_snapshot_from_panels,
    render_dashboard_html,
)
from ccquant.live_price import LiveTape


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


def test_build_snapshot_includes_ohlcv() -> None:
    snap = build_snapshot_from_panels(_synthetic_daily())
    assert len(snap.btc_dates) == len(snap.btc_opens) == len(snap.btc_closes)
    assert len(snap.btc_volumes) == len(snap.btc_closes)
    assert snap.btc_volumes[-1] > 0


def test_monthly_ohlcv_aggregates() -> None:
    from ccquant.dashboard import _monthly_ohlcv

    dates = (
        date(2026, 1, 10),
        date(2026, 1, 20),
        date(2026, 2, 5),
    )
    months, o, h, lo, c, v = _monthly_ohlcv(
        dates,
        (100.0, 110.0, 120.0),
        (105.0, 115.0, 130.0),
        (95.0, 100.0, 118.0),
        (102.0, 112.0, 125.0),
        (10.0, 20.0, 5.0),
    )
    assert months == (date(2026, 1, 1), date(2026, 2, 1))
    assert o == (100.0, 120.0)
    assert h == (115.0, 130.0)
    assert lo == (95.0, 118.0)
    assert c == (112.0, 125.0)
    assert v == (30.0, 5.0)


def test_sma_and_pi_cycle_helpers() -> None:
    from ccquant.dashboard import _cross_events, _sma

    closes = [float(i) for i in range(1, 21)]
    sma5 = _sma(closes, 5)
    assert sma5[3] is None
    assert sma5[4] == pytest.approx(3.0)
    assert sma5[-1] == pytest.approx(18.0)
    dates = [f"2026-01-{i:02d}" for i in range(1, 6)]
    fast: list[float | None] = [1.0, 1.0, 3.0, 3.0, 2.0]
    slow: list[float | None] = [2.0, 2.0, 2.0, 2.0, 2.5]
    up_x, up_y, down_x, down_y = _cross_events(dates, fast, slow)
    assert up_x == ["2026-01-03"]
    assert up_y == [3.0]
    assert down_x == ["2026-01-05"]
    assert down_y == [2.0]


def test_larsson_states() -> None:
    from ccquant.dashboard import _larsson_states

    states = _larsson_states(
        [None, 110.0, 100.0, 100.0],
        [None, 100.0, 110.0, 100.0],
        [None, 10.0, 10.0, 10.0],
        atr_mult=0.3,
    )
    assert states == [None, "bull", "bear", "neutral"]


def test_render_dashboard_html_contains_hero() -> None:
    pytest.importorskip("plotly")
    # Need warm-up length for SMA350 / Pi Cycle seed series.
    snap = build_snapshot_from_panels(_synthetic_daily(n_days=400))
    page = render_dashboard_html(snap)
    assert "ccquant" in page
    assert snap.headline in page
    assert "Outlook" in page
    assert "BTC close" in page
    assert "plotly" in page.lower()
    assert 'data-lt-mode="monthly"' in page
    assert "BTC monthly" in page
    assert "Volume" in page
    assert 'id="lt-ind-sma"' in page
    assert 'id="lt-ind-pi"' in page
    assert 'id="lt-ind-larsson"' in page
    assert "sma50" in page
    assert "pi350x2" in page
    assert "larsson_bull" in page
    # Toggles default unchecked
    assert 'id="lt-ind-sma" checked' not in page
    assert 'id="lt-ind-pi" checked' not in page
    assert 'id="lt-ind-larsson" checked' not in page


def test_render_dashboard_html_includes_live_tape() -> None:
    pytest.importorskip("plotly")
    snap = build_snapshot_from_panels(_synthetic_daily())
    t0 = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    live = LiveTape(
        last=65_432.1,
        change_24h_pct=0.012,
        high_24h=66_000.0,
        low_24h=64_000.0,
        as_of=t0,
        source="binance",
        interval="5m",
        range_key="1h",
        bar_times=(t0 - timedelta(minutes=5), t0),
        bar_opens=(65_000.0, 65_100.0),
        bar_highs=(65_200.0, 65_500.0),
        bar_lows=(64_900.0, 65_050.0),
        bar_closes=(65_100.0, 65_432.1),
    )
    page = render_dashboard_html(snap, live=live)
    assert "LIVE" in page
    assert "65,432.10" in page
    assert "live-candle-plot" in page
    assert 'data-range="1h"' in page
    assert 'data-interval="5m"' in page
    assert "candlestick" in page
    assert "metric-latest" in page
    assert "Daily close" in page
    assert "data-api.binance.vision" in page
    assert "America/Chicago" in page
    assert "America/New_York" in page
    assert 'data-tz="ny"' in page
    assert 'data-tz="utc"' in page
    assert 'data-tz="ct"' in page
    # 12:00 UTC on 2026-07-19 is 07:00 CDT (default seed label)
    assert 'id="live-asof">2026-07-19 07:00 CDT</span>' in page
    assert "ccquant.liveTz" in page
    assert "fetchBinanceKlines" in page
    assert "fetchCoinbaseCandles" in page
    assert '"1d":["1h","4h"]' in page or '"1d": ["1h", "4h"]' in page
    assert '"7d":["4h","1d"]' in page or '"7d": ["4h", "1d"]' in page
    assert "syncIntervalButtons" in page
