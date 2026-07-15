"""Tests for forecasting panel loaders."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ccquant.forecasting import load_hourly_panel, load_wallet_panel
from ccquant.models import HourlyOhlcv
from ccquant.storage import MarketStore


def test_load_hourly_panel_prefers_mart(tmp_path: Path) -> None:
    db = tmp_path / "forecast.duckdb"
    store = MarketStore(db)
    try:
        store.upsert_hourly(
            [
                HourlyOhlcv(
                    symbol="BTC",
                    hour=datetime(2024, 1, 1, 0, 0, 0),
                    open=1.0,
                    high=2.0,
                    low=0.5,
                    close=1.5,
                    volume=10.0,
                    source="binance",
                ),
                HourlyOhlcv(
                    symbol="BTC",
                    hour=datetime(2024, 1, 1, 0, 0, 0),
                    open=9.0,
                    high=9.0,
                    low=9.0,
                    close=9.0,
                    volume=1.0,
                    source="coinbase",
                ),
            ]
        )
        store.connection.execute("create schema if not exists main_marts")
        store.connection.execute(
            """
            create table main_marts.fct_ohlcv_hourly as
            select
              'BTC' as symbol,
              cast('2024-01-01 00:00:00' as timestamp) as hour,
              1.0 as open,
              2.0 as high,
              0.5 as low,
              1.5 as close,
              10.0 as volume,
              'binance' as source
            """
        )
    finally:
        store.close()

    df = load_hourly_panel(db)
    assert len(df) == 1
    assert df["source"][0] == "binance"
    assert df["close"][0] == 1.5


def test_load_hourly_panel_falls_back_to_raw(tmp_path: Path) -> None:
    db = tmp_path / "forecast_raw.duckdb"
    store = MarketStore(db)
    try:
        store.upsert_hourly(
            [
                HourlyOhlcv(
                    symbol="ETH",
                    hour=datetime(2024, 2, 1, 12, 0, 0),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=5.0,
                    source="coinbase",
                ),
            ]
        )
    finally:
        store.close()

    df = load_hourly_panel(db)
    assert len(df) == 1
    assert df["symbol"][0] == "ETH"


def test_load_wallet_panel_empty_without_mart(tmp_path: Path) -> None:
    db = tmp_path / "wallet_panel.duckdb"
    store = MarketStore(db)
    store.close()
    df = load_wallet_panel(db)
    assert df.is_empty()
