from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ccquant.sources import fetch_binance_daily

KLINE_ROW = [
    [
        1719792000000,
        "60000.0",
        "61000.0",
        "59000.0",
        "60500.0",
        "123.45",
        1719878399999,
        "0",
        100,
        "0",
        "0",
        "0",
    ]
]


def _response() -> httpx.Response:
    request = httpx.Request("GET", "https://api.binance.com/api/v3/klines")
    return httpx.Response(200, json=KLINE_ROW, request=request)


@pytest.mark.asyncio
async def test_fetch_binance_daily_parses_kline() -> None:
    mock_get = AsyncMock(return_value=_response())
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            candles = await fetch_binance_daily(
                client,
                symbol="BTC",
                pair="BTCUSDT",
                start=date(2024, 7, 1),
                end=date(2024, 7, 1),
            )
    assert len(candles) == 1
    assert candles[0].symbol == "BTC"
    assert candles[0].close == 60500.0

