"""Tests for the lightweight Market Tracker dashboard."""

from __future__ import annotations

import json
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


def test_btc_volume_signal_sponsored_and_mtd() -> None:
    from ccquant.dashboard import _btc_volume_signal

    end = date(2026, 3, 10)
    dates: list[date] = []
    volumes: list[float] = []
    # Quiet baseline months; heavy March MTD + elevated last print.
    for i in range(90):
        d = end - timedelta(days=89 - i)
        dates.append(d)
        volumes.append(4_000.0 if d.month == 3 else 1_000.0)
    volumes[-1] = 5_000.0
    sig, label, rel, mtd = _btc_volume_signal(
        dates, volumes, as_of=end, ret_7d=0.05
    )
    assert rel is not None and rel > 1.2
    assert mtd is not None and mtd > 1.0
    assert sig == 1
    assert "sponsored" in label
    assert "MTD" in label

    sig_down, label_down, _, _ = _btc_volume_signal(
        dates, volumes, as_of=end, ret_7d=-0.05
    )
    assert sig_down == -1
    assert "distribution" in label_down


def test_build_snapshot_includes_volume_chip_fields() -> None:
    snap = build_snapshot_from_panels(_synthetic_daily(n_days=120))
    assert snap.vol_label
    assert snap.rel_vol_20 is not None
    assert snap.mtd_vol_ratio is not None
    assert snap.vol_signal in {-1, 0, 1}


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


def test_btc_monthly_gains_seed_matrix() -> None:
    from ccquant.dashboard import _btc_monthly_gains_seed

    # Two full months of BTC so Feb has a measurable MoM return.
    end = date(2025, 2, 28)
    rows: list[dict[str, object]] = []
    for i in range(60):
        d = end - timedelta(days=59 - i)
        # Jan ends ~100, Feb ends ~110 → ~+10% Feb
        close = 100.0 if d.month == 1 else 110.0
        rows.append(
            {
                "symbol": "BTC",
                "date": d,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1.0,
                "source": "test",
            }
        )
    snap = build_snapshot_from_panels(pl.DataFrame(rows))
    seed = _btc_monthly_gains_seed(snap)
    assert seed["months"][0] == "Jan"
    assert seed["months"][-1] == "Dec"
    assert "2025" in seed["years"]
    z = seed["z"]
    assert isinstance(z, list) and z
    # Find 2025 row; February (index 1) should be ~+10%.
    years = seed["years"]
    assert isinstance(years, list)
    row = z[years.index("2025")]
    assert isinstance(row, list)
    feb = row[1]
    assert feb is not None
    assert feb == pytest.approx(10.0, abs=0.05)
    zmax = seed["zmax"]
    zmin = seed["zmin"]
    assert isinstance(zmax, float) and isinstance(zmin, float)
    assert zmin == -zmax
    assert zmax > 0
    assert seed["current_month"] == "Feb"
    assert seed["current_year"] == "2025"
    texts = seed["text"]
    assert isinstance(texts, list)
    feb_txt = texts[years.index("2025")][1]
    assert feb_txt == "+10.0"


def test_heatmap_marks_open_month_as_mtd() -> None:
    from ccquant.dashboard import _btc_monthly_gains_seed

    end = date(2026, 8, 10)
    rows: list[dict[str, object]] = []
    for i in range(45):
        d = end - timedelta(days=44 - i)
        close = 100.0 if d.month == 7 else 101.8
        rows.append(
            {
                "symbol": "BTC",
                "date": d,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1.0,
                "source": "test",
            }
        )
    snap = build_snapshot_from_panels(pl.DataFrame(rows))
    seed = _btc_monthly_gains_seed(snap)
    years = seed["years"]
    assert isinstance(years, list)
    texts = seed["text"]
    assert isinstance(texts, list)
    aug = texts[years.index("2026")][7]
    assert "MTD" in str(aug)


def test_extend_daily_panel_with_raw_appends_newer_btc() -> None:
    from ccquant.dashboard import _extend_daily_panel_with_raw

    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "source"]
    mart = pl.DataFrame(
        [
            {
                "symbol": "BTC",
                "date": date(2026, 8, 10),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "source": "mart",
            }
        ]
    ).select(cols)
    raw = pl.DataFrame(
        [
            {
                "symbol": "BTC",
                "date": date(2026, 8, 10),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "source": "raw",
            },
            {
                "symbol": "BTC",
                "date": date(2026, 8, 24),
                "open": 2.0,
                "high": 2.0,
                "low": 2.0,
                "close": 2.0,
                "volume": 2.0,
                "source": "raw",
            },
        ]
    ).select(cols)
    out = _extend_daily_panel_with_raw(mart, raw)
    btc = out.filter(pl.col("symbol") == "BTC").sort("date")
    assert btc.height == 2
    assert btc["date"][-1] == date(2026, 8, 24)
    assert btc["close"][-1] == pytest.approx(2.0)


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


def test_larsson_regime_bands_collapses_runs() -> None:
    from ccquant.dashboard import _larsson_regime_bands

    dates = [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
    ]
    states: list[str | None] = [None, "bull", "bull", "bear", "neutral"]
    bands = _larsson_regime_bands(dates, states, bar="daily")
    assert bands == [
        {"start": "2024-01-02", "end": "2024-01-04", "state": "bull"},
        {"start": "2024-01-04", "end": "2024-01-05", "state": "bear"},
    ]


def test_pres_cycle_year_almanac_numbering() -> None:
    from ccquant.dashboard import _pres_cycle_year

    assert _pres_cycle_year(2024) == 4
    assert _pres_cycle_year(2025) == 1
    assert _pres_cycle_year(2026) == 2
    assert _pres_cycle_year(2027) == 3
    assert _pres_cycle_year(2028) == 4


def test_halving_overlay_marks_subsidy_cuts() -> None:
    from ccquant.dashboard import _halving_overlay

    hv = _halving_overlay()
    events = hv["events"]
    assert isinstance(events, list)
    by_id = {e["id"]: e for e in events if isinstance(e, dict)}
    assert by_id["H1"]["date"] == "2012-11-28"
    assert by_id["H4"]["date"] == "2024-04-20"
    assert by_id["H4"]["label"] == "Halving 2024"
    assert by_id["H5"]["estimated"] is True
    epochs = hv["epochs"]
    assert isinstance(epochs, list)
    assert len(epochs) == 5
    last = epochs[-1]
    assert isinstance(last, dict)
    assert last["start"] == "2024-04-20"
    assert last["reward"] == "3.125 BTC"


def test_presidential_overlay_terms_and_elections() -> None:
    from ccquant.dashboard import _presidential_overlay

    pc = _presidential_overlay(until=date(2026, 8, 19))
    years = {b["year"]: b for b in pc["years"] if isinstance(b, dict)}
    assert years[2024]["cycle"] == 4
    assert years[2024]["short"] == "Y4"
    assert years[2026]["label"] == "Y2 midterm"
    admins = {a["label"]: a for a in pc["admins"] if isinstance(a, dict)}
    assert admins["Trump II"]["start"] == "2025-01-20"
    assert admins["Biden"]["end"] == "2025-01-20"
    election_dates = {e["date"] for e in pc["elections"] if isinstance(e, dict)}
    assert "2024-11-05" in election_dates
    assert "2028-11-07" in election_dates


def test_merge_live_bar_appends_today() -> None:
    from ccquant.dashboard import _merge_live_bar

    dates = [date(2026, 8, 23)]
    opens = [100.0]
    highs = [110.0]
    lows = [90.0]
    closes = [105.0]
    volumes = [1.0]
    live = LiveTape(
        last=108.0,
        change_24h_pct=0.01,
        high_24h=109.0,
        low_24h=104.0,
        as_of=datetime(2026, 8, 24, 18, 0, tzinfo=UTC),
        source="binance",
        interval="5m",
        range_key="1h",
        bar_times=(),
        bar_opens=(),
        bar_highs=(),
        bar_lows=(),
        bar_closes=(),
    )
    _merge_live_bar(
        dates, opens, highs, lows, closes, volumes, live, through=date(2026, 8, 24)
    )
    assert dates[-1] == date(2026, 8, 24)
    assert closes[-1] == pytest.approx(108.0)
    assert opens[-1] == pytest.approx(105.0)


def test_merge_live_bar_updates_same_day() -> None:
    from ccquant.dashboard import _merge_live_bar

    dates = [date(2026, 8, 24)]
    opens = [100.0]
    highs = [110.0]
    lows = [90.0]
    closes = [105.0]
    volumes = [1.0]
    live = LiveTape(
        last=112.0,
        change_24h_pct=0.02,
        high_24h=113.0,
        low_24h=104.0,
        as_of=datetime(2026, 8, 24, 22, 0, tzinfo=UTC),
        source="binance",
        interval="5m",
        range_key="1h",
        bar_times=(),
        bar_opens=(),
        bar_highs=(),
        bar_lows=(),
        bar_closes=(),
    )
    _merge_live_bar(
        dates, opens, highs, lows, closes, volumes, live, through=date(2026, 8, 24)
    )
    assert len(dates) == 1
    assert closes[-1] == pytest.approx(112.0)
    assert highs[-1] == pytest.approx(112.0)
    from ccquant.dashboard import _presidential_overlay

    pc = _presidential_overlay(until=date(2026, 8, 19))
    years = {b["year"]: b for b in pc["years"] if isinstance(b, dict)}
    assert years[2024]["cycle"] == 4
    assert years[2024]["short"] == "Y4"
    assert years[2026]["label"] == "Y2 midterm"
    admins = {a["label"]: a for a in pc["admins"] if isinstance(a, dict)}
    assert admins["Trump II"]["start"] == "2025-01-20"
    assert admins["Biden"]["end"] == "2025-01-20"
    election_dates = {e["date"] for e in pc["elections"] if isinstance(e, dict)}
    assert "2024-11-05" in election_dates
    assert "2028-11-07" in election_dates


def test_render_dashboard_html_contains_hero() -> None:
    pytest.importorskip("plotly")
    # Need warm-up length for SMA350 / Pi Cycle seed series.
    snap = build_snapshot_from_panels(_synthetic_daily(n_days=400))
    page = render_dashboard_html(snap)
    assert "ccquant" in page
    assert snap.headline in page
    assert "Outlook" in page
    assert "BTC close" in page or "BTC daily" in page
    assert "plotly" in page.lower()
    assert 'data-lt-bar="monthly"' in page
    assert 'data-lt-style="candle"' in page
    assert 'data-lt-length="mtd"' in page
    assert 'data-lt-length="qtd"' in page
    assert 'data-lt-length="ytd"' in page
    assert 'data-lt-length="3m"' in page
    assert 'data-lt-length="2y"' in page
    assert 'data-lt-length="all"' in page
    assert 'id="lt-periods"' in page
    assert 'id="lt-ind-sma"' in page
    assert 'id="lt-ind-pi"' in page
    assert 'id="lt-ind-larsson"' in page
    assert 'id="lt-ind-halving"' in page
    assert 'id="lt-ind-pres"' in page
    assert 'id="lt-ind-clear"' in page
    assert 'id="lt-cycle-legend"' in page
    assert "halvingShapes" in page
    assert "presShapes" in page
    assert "overlayAnnotations" in page
    assert "updateCycleLegend" in page
    assert "visibleMid" in page
    assert "captureevents" in page
    assert "todayShapes" in page
    assert "sliderRange" in page
    assert "through_date" in page
    assert "live_from" in page
    assert "sma50" in page
    assert "pi350x2" in page
    assert "larsson_bull" in page
    assert "larsson_bands" in page
    assert '"monthly"' in page
    assert "larssonShapes" in page
    assert "length_starts" in page
    assert "rangeslider" in page
    assert "renderChart" in page
    assert "BTC monthly gains" in page
    assert 'id="btc-month-heatmap"' in page
    assert 'id="btc-month-heatmap-seed"' in page
    assert '"type":"heatmap"' in page or "type: \"heatmap\"" in page
    assert 'tickmode: "array"' in page
    assert "categoryarray: months" in page
    assert "categoryarray: years" in page
    assert 'text: "Month"' in page
    assert 'text: "Year"' in page
    assert "current_month" in page
    assert "rgba(247, 147, 26" in page
    assert "Rel vol 20d" in page
    assert "MTD vol pace" in page
    assert ">Volume<" in page or "Volume" in page
    # Default viewport is ~2y when history is longer than that.
    long_page = render_dashboard_html(
        build_snapshot_from_panels(_synthetic_daily(n_days=1200))
    )
    seed = json.loads(
        long_page.split('id="lt-seed">', 1)[1].split("</script>", 1)[0]
    )
    assert seed["default_length"] == "2y"
    assert seed["length_starts"]["2y"] is not None
    assert seed["dates"][0] < seed["length_starts"]["2y"]
    assert seed["length_starts"]["2y"] < seed["dates"][-1]
    assert "mtd" in seed["length_starts"]
    assert "qtd" in seed["length_starts"]
    assert "ytd" in seed["length_starts"]
    assert "3m" in seed["length_starts"]
    from ccquant.dashboard import _chart_period_start, _session_today

    today = _session_today()
    assert seed["length_starts"]["mtd"] == _chart_period_start(today, "mtd").isoformat()
    assert seed["length_starts"]["qtd"] == _chart_period_start(today, "qtd").isoformat()
    assert seed["length_starts"]["ytd"] == _chart_period_start(today, "ytd").isoformat()
    assert "open" in seed and "high" in seed
    assert "monthly" in seed and "larsson_bands" in seed["monthly"]
    assert "halvings" in seed and "pres_cycle" in seed
    assert "through_date" in seed and "live_from" in seed
    assert isinstance(seed["through_date"], str)
    assert seed["through_date"] >= seed["dates"][-1]
    if seed["live_from"] is not None:
        assert seed["live_from"] <= seed["through_date"]
    hv = seed["halvings"]
    assert isinstance(hv, dict)
    events = hv["events"]
    assert isinstance(events, list)
    event_dates = {e["date"] for e in events if isinstance(e, dict)}
    assert "2012-11-28" in event_dates
    assert "2024-04-20" in event_dates
    assert any(isinstance(e, dict) and e.get("estimated") for e in events)
    epochs = hv["epochs"]
    assert isinstance(epochs, list) and len(epochs) == 5
    pc = seed["pres_cycle"]
    assert isinstance(pc, dict)
    years = {b["year"]: b["cycle"] for b in pc["years"] if isinstance(b, dict)}
    assert years[2024] == 4
    assert years[2025] == 1
    assert years[2026] == 2
    admin_labels = {a["label"] for a in pc["admins"] if isinstance(a, dict)}
    assert "Trump II" in admin_labels
    assert "Biden" in admin_labels
    # Toggles default unchecked
    assert 'id="lt-ind-sma" checked' not in page
    assert 'id="lt-ind-pi" checked' not in page
    assert 'id="lt-ind-larsson" checked' not in page
    assert 'id="lt-ind-halving" checked' not in page
    assert 'id="lt-ind-pres" checked' not in page


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
    assert '"7d":["1h","4h","1d"]' in page or '"7d": ["1h", "4h", "1d"]' in page
    assert "syncIntervalButtons" in page
    lt_seed = json.loads(
        page.split('id="lt-seed">', 1)[1].split("</script>", 1)[0]
    )
    assert lt_seed["dates"][-1] >= "2026-07-19"
    assert lt_seed["close"][-1] == pytest.approx(65_432.1)
    assert lt_seed["through_date"] >= "2026-07-19"
    assert lt_seed["live_from"] is not None
    assert lt_seed["live_label"] == "Live"
