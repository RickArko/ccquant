"""Tests for month-over-month helpers."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from ccquant.mom import (
    compare_trailing_windows,
    full_months,
    market_mom,
    monthly_mom,
    ohlcv_price_mom,
    top_mom_months,
)


def _daily_level(start: date, n: int, values: list[float]) -> pl.DataFrame:
    assert len(values) == n
    return pl.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(n)],
            "value": values,
        }
    )


def test_monthly_mom_last_and_sum() -> None:
    # Jan: 10..12, Feb: 20..22 → EOM 12→22 (+83.3%), sum 33→63 (+90.9%)
    rows: list[dict[str, object]] = []
    for d, v in (
        (date(2024, 1, 10), 10.0),
        (date(2024, 1, 20), 11.0),
        (date(2024, 1, 31), 12.0),
        (date(2024, 2, 5), 20.0),
        (date(2024, 2, 28), 22.0),
        (date(2024, 2, 15), 21.0),
    ):
        rows.append({"date": d, "value": v})
    df = pl.DataFrame(rows)

    eom = monthly_mom(df, value_col="value", agg="last", as_of=date(2024, 3, 1))
    assert eom.height == 2
    assert not bool(eom["partial"].any())
    assert abs(float(eom["value"][0]) - 12.0) < 1e-9
    assert abs(float(eom["value"][1]) - 22.0) < 1e-9
    assert abs(float(eom["value_mom"][1]) - (22 / 12 - 1)) < 1e-9

    summed = monthly_mom(df, value_col="value", agg="sum", as_of=date(2024, 3, 1))
    assert abs(float(summed["value"][0]) - 33.0) < 1e-9
    assert abs(float(summed["value"][1]) - 63.0) < 1e-9


def test_mark_partial_and_trailing_windows() -> None:
    df = pl.DataFrame(
        {
            "date": [
                date(2024, 1, 31),
                date(2024, 2, 29),
                date(2024, 3, 31),
                date(2024, 4, 30),
                date(2024, 5, 31),
                date(2024, 6, 15),  # partial June if as_of mid-month
            ],
            "tvl": [100.0, 110.0, 120.0, 130.0, 140.0, 145.0],
        }
    )
    monthly = monthly_mom(df, value_col="tvl", agg="last", as_of=date(2024, 6, 15))
    assert bool(monthly.filter(pl.col("month") == date(2024, 6, 1))["partial"][0])
    full = full_months(monthly)
    assert full.height == 5

    cmp_ = compare_trailing_windows(monthly, value_col="tvl", window=2, how="last")
    # Full months Jan..May; last 4 → prior ends Mar=120, recent ends May=140.
    assert cmp_["prior"] == 120.0
    assert cmp_["recent"] == 140.0
    assert cmp_["change"] is not None
    assert abs(float(cmp_["change"]) - (140 / 120 - 1)) < 1e-9


def test_ohlcv_price_mom_and_market() -> None:
    rows: list[dict[str, object]] = []
    # BTC flat Jan then +10% Feb; ETH +20% Feb; SOL -10% Feb
    specs = {
        "BTC": (100.0, 110.0),
        "ETH": (50.0, 60.0),
        "SOL": (20.0, 18.0),
    }
    for sym, (jan, feb) in specs.items():
        rows.append(
            {
                "symbol": sym,
                "date": date(2024, 1, 31),
                "close": jan,
                "volume": 1000.0,
            }
        )
        rows.append(
            {
                "symbol": sym,
                "date": date(2024, 2, 29),
                "close": feb,
                "volume": 2000.0,
            }
        )
    panel = pl.DataFrame(rows)

    prices = ohlcv_price_mom(panel, symbols=["BTC", "ETH"], as_of=date(2024, 3, 1))
    assert set(prices["symbol"].to_list()) == {"BTC", "ETH"}
    btc_feb = prices.filter(
        (pl.col("symbol") == "BTC") & (pl.col("month") == date(2024, 2, 1))
    )
    assert abs(float(btc_feb["close_mom"][0]) - 0.1) < 1e-9

    mkt = market_mom(panel, as_of=date(2024, 3, 1))
    feb = mkt.filter(pl.col("month") == date(2024, 2, 1))
    # eq weight: (0.10 + 0.20 + -0.10) / 3 = 0.0666...
    assert abs(float(feb["eq_weight_mom"][0]) - (0.1 + 0.2 - 0.1) / 3) < 1e-9
    assert int(feb["n_up"][0]) == 2
    assert int(feb["n_down"][0]) == 1
    assert float(feb["volume"][0]) == 6000.0


def test_top_mom_months() -> None:
    monthly = pl.DataFrame(
        {
            "month": [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)],
            "x_mom": [0.1, 0.5, -0.2],
            "partial": [False, False, False],
        }
    )
    top = top_mom_months(monthly, mom_col="x_mom", n=1)
    assert top["month"][0] == date(2024, 2, 1)
