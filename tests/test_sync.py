from __future__ import annotations

from datetime import date

import httpx
import pytest

from ccquant.config import AppConfig, UniverseConfig
from ccquant.models import Asset
from ccquant.storage import MarketStore
from ccquant.sync import MarketSync


@pytest.mark.asyncio
async def test_backfill_records_zero_and_continues_on_http_error(
    tmp_path,
    monkeypatch,
) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    as_of = date(2026, 7, 2)
    store.replace_assets(
        [
            Asset(
                rank=1,
                symbol="DOGE",
                coingecko_id="dogecoin",
                binance_pair=None,
                coinbase_product_id="DOGE-USD",
                active=True,
                as_of_date=as_of,
            ),
            Asset(
                rank=2,
                symbol="BTC",
                coingecko_id="bitcoin",
                binance_pair="BTCUSDT",
                coinbase_product_id="BTC-USD",
                active=True,
                as_of_date=as_of,
            ),
        ],
        as_of,
    )

    async def fake_daily(self: MarketSync, asset: Asset, *, full: bool) -> int:
        if asset.symbol == "DOGE":
            request = httpx.Request("GET", "https://api.coinbase.com")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError(
                "Service unavailable",
                request=request,
                response=response,
            )
        return 5

    monkeypatch.setattr(MarketSync, "backfill_daily", fake_daily)
    syncer = MarketSync(
        store,
        AppConfig(
            database=tmp_path / "ccquant.duckdb",
            universe=UniverseConfig(request_delay_seconds=0),
        ),
    )
    try:
        results = await syncer.backfill(interval="1d", full=True)
    finally:
        await syncer.close()
        store.close()

    assert results == {"DOGE": 0, "BTC": 5}

