from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ccquant.sources import (
    extract_depth_features,
    fetch_binance_daily,
    fetch_binance_depth,
    fetch_binance_oi,
    fetch_bybit_depth,
    fetch_bybit_oi,
    fetch_defillama_prices,
    fetch_fred_series,
    fetch_okx_depth,
    fetch_okx_oi,
)

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


OI_ROW = [
    {
        "symbol": "BTCUSDT",
        "sumOpenInterest": "20403.637",
        "sumOpenInterestValue": "150570784.07",
        "timestamp": "1719792000000",
    }
]


def _oi_response() -> httpx.Response:
    request = httpx.Request(
        "GET", "https://fapi.binance.com/futures/data/openInterestHist"
    )
    return httpx.Response(200, json=OI_ROW, request=request)


@pytest.mark.asyncio
async def test_fetch_binance_oi_parses_response() -> None:
    mock_get = AsyncMock(return_value=_oi_response())
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_binance_oi(
                client,
                symbol="BTC",
                pair="BTCUSDT",
                interval="1h",
                start=datetime(2024, 6, 30, tzinfo=UTC),
                end=datetime(2024, 7, 1, tzinfo=UTC),
            )
    assert len(points) == 1
    assert points[0].symbol == "BTC"
    assert points[0].exchange == "binance"
    assert points[0].unit == "usd_notional"
    assert points[0].open_interest == 150570784.07
    assert points[0].interval == "1h"


@pytest.mark.asyncio
async def test_fetch_binance_oi_returns_empty_on_400() -> None:
    request = httpx.Request(
        "GET", "https://fapi.binance.com/futures/data/openInterestHist"
    )
    mock_get = AsyncMock(
        return_value=httpx.Response(400, json={}, request=request)
    )
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_binance_oi(
                client,
                symbol="BAD",
                pair="BADUSDT",
                interval="1h",
                start=datetime(2024, 6, 30, tzinfo=UTC),
                end=datetime(2024, 7, 1, tzinfo=UTC),
            )
    assert points == []


BYBIT_RESPONSE = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "category": "linear",
        "symbol": "BTCUSDT",
        "list": [
            {
                "openInterest": "20403.637",
                "timestamp": "1719792000000",
            }
        ],
        "nextPageCursor": "",
    },
}


def _bybit_response() -> httpx.Response:
    request = httpx.Request("GET", "https://api.bybit.com/v5/market/open-interest")
    return httpx.Response(200, json=BYBIT_RESPONSE, request=request)


@pytest.mark.asyncio
async def test_fetch_bybit_oi_parses_response() -> None:
    mock_get = AsyncMock(return_value=_bybit_response())
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_bybit_oi(
                client,
                symbol="BTC",
                pair="BTCUSDT",
                interval="1h",
                start=datetime(2024, 6, 30, tzinfo=UTC),
                end=datetime(2024, 7, 1, tzinfo=UTC),
            )
    assert len(points) == 1
    assert points[0].symbol == "BTC"
    assert points[0].exchange == "bybit"
    assert points[0].unit == "coin"
    assert points[0].open_interest == 20403.637
    assert points[0].interval == "1h"


@pytest.mark.asyncio
async def test_fetch_bybit_oi_returns_empty_on_error_retcodes() -> None:
    error_response = {
        "retCode": 10001,
        "retMsg": "params error",
        "result": {},
    }
    request = httpx.Request("GET", "https://api.bybit.com/v5/market/open-interest")
    mock_get = AsyncMock(
        return_value=httpx.Response(200, json=error_response, request=request)
    )
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_bybit_oi(
                client,
                symbol="BAD",
                pair="BADUSDT",
                interval="1h",
                start=datetime(2024, 6, 30, tzinfo=UTC),
                end=datetime(2024, 7, 1, tzinfo=UTC),
            )
    assert points == []


OKX_RESPONSE = {
    "code": "0",
    "data": [
        {
            "instId": "BTC-USDT-SWAP",
            "oi": "100000",
            "oiCcy": "204.036",
            "ts": "1719792000000",
        }
    ],
}


def _okx_response() -> httpx.Response:
    request = httpx.Request(
        "GET", "https://www.okx.com/api/v5/market/history-open-interest"
    )
    return httpx.Response(200, json=OKX_RESPONSE, request=request)


@pytest.mark.asyncio
async def test_fetch_okx_oi_parses_response() -> None:
    mock_get = AsyncMock(return_value=_okx_response())
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_okx_oi(
                client,
                symbol="BTC",
                interval="1h",
                start=datetime(2024, 6, 30, tzinfo=UTC),
                end=datetime(2024, 7, 1, tzinfo=UTC),
            )
    assert len(points) == 1
    assert points[0].symbol == "BTC"
    assert points[0].exchange == "okx"
    assert points[0].unit == "coin"
    assert points[0].open_interest == 204.036
    assert points[0].interval == "1h"


@pytest.mark.asyncio
async def test_fetch_okx_oi_returns_empty_on_error_code() -> None:
    error_response = {"code": "50011", "msg": "rate limit", "data": []}
    request = httpx.Request(
        "GET", "https://www.okx.com/api/v5/market/history-open-interest"
    )
    mock_get = AsyncMock(
        return_value=httpx.Response(200, json=error_response, request=request)
    )
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_okx_oi(
                client,
                symbol="BAD",
                interval="1h",
                start=datetime(2024, 6, 30, tzinfo=UTC),
                end=datetime(2024, 7, 1, tzinfo=UTC),
            )
    assert points == []


FRED_RESPONSE = {
    "observations": [
        {"date": "2024-07-01", "value": "5.33"},
        {"date": "2024-07-02", "value": "5.34"},
        {"date": "2024-07-03", "value": "."},
    ]
}


def _fred_response() -> httpx.Response:
    request = httpx.Request("GET", "https://api.stlouisfed.org/fred/series/observations")
    return httpx.Response(200, json=FRED_RESPONSE, request=request)


@pytest.mark.asyncio
async def test_fetch_fred_series_parses_observations() -> None:
    mock_get = AsyncMock(return_value=_fred_response())
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_fred_series(
                client,
                series_id="DGS10",
                api_key="test_key",
                start=date(2024, 7, 1),
                end=date(2024, 7, 3),
            )
    assert len(points) == 2
    assert points[0].series_id == "DGS10"
    assert points[0].value == 5.33
    assert points[0].source == "fred"
    assert points[1].value == 5.34


def test_extract_depth_features_computes_mid_spread_and_bands() -> None:
    # mid = 100; 25bps band = ±0.25; levels at 99.9 / 100.1 inside 25bps
    bids = [(99.9, 10.0), (99.0, 5.0)]
    asks = [(100.1, 8.0), (101.0, 4.0)]
    features = extract_depth_features(bids, asks)
    assert features["mid"] == pytest.approx(100.0)
    assert features["spread_bps"] == pytest.approx(20.0)
    assert features["bid_notional_bps_25"] == pytest.approx(99.9 * 10.0)
    assert features["ask_notional_bps_25"] == pytest.approx(100.1 * 8.0)
    assert features["imbalance_bps_25"] is not None
    assert features["depth_levels"] == 4


def test_extract_depth_features_empty_book() -> None:
    assert extract_depth_features([], [(100.0, 1.0)]) == {}
    assert extract_depth_features([(100.0, 1.0)], []) == {}


BINANCE_DEPTH = {
    "lastUpdateId": 1027024,
    "bids": [["99.9", "10.0"], ["99.0", "5.0"]],
    "asks": [["100.1", "8.0"], ["101.0", "4.0"]],
}


def _binance_depth_response() -> httpx.Response:
    request = httpx.Request("GET", "https://api.binance.com/api/v3/depth")
    return httpx.Response(200, json=BINANCE_DEPTH, request=request)


@pytest.mark.asyncio
async def test_fetch_binance_depth_parses_response() -> None:
    mock_get = AsyncMock(return_value=_binance_depth_response())
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            snap = await fetch_binance_depth(
                client, symbol="BTC", pair="BTCUSDT", limit=100
            )
    assert snap is not None
    assert snap.symbol == "BTC"
    assert snap.exchange == "binance"
    assert snap.mid == pytest.approx(100.0)
    assert snap.last_update_id == 1027024


@pytest.mark.asyncio
async def test_fetch_binance_depth_returns_none_on_400() -> None:
    request = httpx.Request("GET", "https://api.binance.com/api/v3/depth")
    mock_get = AsyncMock(
        return_value=httpx.Response(400, json={}, request=request)
    )
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            snap = await fetch_binance_depth(
                client, symbol="BAD", pair="BADUSDT"
            )
    assert snap is None


BYBIT_DEPTH = {
    "retCode": 0,
    "result": {
        "s": "BTCUSDT",
        "b": [["99.9", "10.0"]],
        "a": [["100.1", "8.0"]],
        "ts": 1719792000000,
        "u": 12345,
    },
}


@pytest.mark.asyncio
async def test_fetch_bybit_depth_parses_response() -> None:
    request = httpx.Request("GET", "https://api.bybit.com/v5/market/orderbook")
    mock_get = AsyncMock(
        return_value=httpx.Response(200, json=BYBIT_DEPTH, request=request)
    )
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            snap = await fetch_bybit_depth(
                client, symbol="BTC", pair="BTCUSDT"
            )
    assert snap is not None
    assert snap.exchange == "bybit"
    assert snap.last_update_id == 12345


OKX_DEPTH = {
    "code": "0",
    "data": [
        {
            "bids": [["99.9", "10.0", "0", "1"]],
            "asks": [["100.1", "8.0", "0", "1"]],
            "ts": "1719792000000",
            "seqId": 999,
        }
    ],
}


@pytest.mark.asyncio
async def test_fetch_okx_depth_parses_response() -> None:
    request = httpx.Request("GET", "https://www.okx.com/api/v5/market/books")
    mock_get = AsyncMock(
        return_value=httpx.Response(200, json=OKX_DEPTH, request=request)
    )
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            snap = await fetch_okx_depth(client, symbol="BTC")
    assert snap is not None
    assert snap.exchange == "okx"
    assert snap.last_update_id == 999


DEFILLAMA_PRICES = {
    "coins": {
        "coingecko:bitcoin": {
            "price": 65000.0,
            "symbol": "BTC",
            "timestamp": 1719792000,
        }
    }
}


@pytest.mark.asyncio
async def test_fetch_defillama_prices_parses_response() -> None:
    request = httpx.Request("GET", "https://coins.llama.fi/prices/current/x")
    mock_get = AsyncMock(
        return_value=httpx.Response(
            200, json=DEFILLAMA_PRICES, request=request
        )
    )
    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", mock_get):
            points = await fetch_defillama_prices(
                client,
                symbol_to_coingecko_id={"BTC": "bitcoin"},
            )
    assert len(points) == 1
    assert points[0].symbol == "BTC"
    assert points[0].price_usd == 65000.0
    assert points[0].venue == "defillama"

