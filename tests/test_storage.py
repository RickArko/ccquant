from __future__ import annotations

from datetime import UTC, date, datetime

from ccquant.models import Asset, DailyOhlcv, HourlyOhlcv
from ccquant.storage import MarketStore


def test_store_assets_and_ohlcv(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        as_of = date(2026, 7, 2)
        store.replace_assets(
            [
                Asset(
                    rank=1,
                    symbol="BTC",
                    coingecko_id="bitcoin",
                    binance_pair="BTCUSDT",
                    coinbase_product_id="BTC-USD",
                    active=True,
                    as_of_date=as_of,
                )
            ],
            as_of,
        )
        assert store.active_assets()[0].symbol == "BTC"

        assert store.upsert_daily(
            [
                DailyOhlcv(
                    symbol="BTC",
                    date=date(2026, 7, 1),
                    open=100.0,
                    high=110.0,
                    low=95.0,
                    close=105.0,
                    volume=10.0,
                    source="binance",
                )
            ]
        ) == 1
        assert store.upsert_hourly(
            [
                HourlyOhlcv(
                    symbol="BTC",
                    hour=datetime(2026, 7, 1, 12, tzinfo=UTC),
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1.0,
                    source="binance",
                )
            ]
        ) == 1
        status = store.status_rows()
        assert status[0]["daily_rows"] == 1
        assert status[0]["hourly_rows"] == 1
    finally:
        store.close()

