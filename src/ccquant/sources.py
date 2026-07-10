from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx

from ccquant.models import DailyOhlcv, HourlyOhlcv, MacroPoint, OpenInterest

BINANCE_API = "https://api.binance.com"
BINANCE_FAPI = "https://fapi.binance.com"
BYBIT_API = "https://api.bybit.com"
OKX_API = "https://www.okx.com"
COINBASE_API = "https://api.coinbase.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"
FRED_API = "https://api.stlouisfed.org"
MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000
COINBASE_DAILY_CHUNK_DAYS = 300
COINBASE_HOURLY_CHUNK_DAYS = 14
COINGECKO_CHUNK_DAYS = 180


def default_binance_pair(symbol: str) -> str:
    return f"{symbol.upper()}USDT"


def coinbase_product_id(symbol: str) -> str:
    return f"{symbol.upper()}-USD"


async def probe_binance_pair(client: httpx.AsyncClient, pair: str) -> bool:
    resp = await client.get(
        f"{BINANCE_API}/api/v3/klines",
        params={"symbol": pair.upper(), "interval": "1d", "limit": 1},
    )
    return resp.status_code == 200


async def probe_coinbase_product(client: httpx.AsyncClient, product_id: str) -> bool:
    resp = await client.get(
        f"{COINBASE_API}/api/v3/brokerage/market/products/{product_id.upper()}"
    )
    return resp.status_code == 200


async def fetch_top_markets(
    client: httpx.AsyncClient,
    *,
    size: int,
) -> list[dict[str, str | int]]:
    pages = (size + 99) // 100
    markets: list[dict[str, str | int]] = []
    for page in range(1, pages + 1):
        resp = await client.get(
            f"{COINGECKO_API}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": min(100, size - len(markets)),
                "page": page,
                "sparkline": "false",
            },
        )
        resp.raise_for_status()
        for item in resp.json():
            markets.append(
                {
                    "rank": len(markets) + 1,
                    "symbol": str(item["symbol"]).upper(),
                    "coingecko_id": str(item["id"]),
                }
            )
            if len(markets) >= size:
                break
    return markets[:size]


async def fetch_binance_daily(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    pair: str,
    start: date | None = None,
    end: date | None = None,
) -> list[DailyOhlcv]:
    candles: list[DailyOhlcv] = []
    start_ms = _date_ms(start, end_of_day=False) if start else None
    end_ms = _date_ms(end, end_of_day=True) if end else None
    while True:
        params: dict[str, str | int] = {
            "symbol": pair.upper(),
            "interval": "1d",
            "limit": 1000,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        resp = await client.get(f"{BINANCE_API}/api/v3/klines", params=params)
        if resp.status_code == 400:
            return []
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for row in batch:
            day = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC).date()
            candles.append(
                DailyOhlcv(
                    symbol=symbol.upper(),
                    date=day,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    source="binance",
                )
            )
        if len(batch) < 1000:
            break
        start_ms = int(batch[-1][0]) + MS_PER_DAY
    return candles


async def fetch_binance_hourly(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    pair: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[HourlyOhlcv]:
    candles: list[HourlyOhlcv] = []
    start_ms = int(start.timestamp() * 1000) if start else None
    end_ms = int(end.timestamp() * 1000) if end else None
    while True:
        params: dict[str, str | int] = {
            "symbol": pair.upper(),
            "interval": "1h",
            "limit": 1000,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        resp = await client.get(f"{BINANCE_API}/api/v3/klines", params=params)
        if resp.status_code == 400:
            return []
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for row in batch:
            hour = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC).replace(
                minute=0, second=0, microsecond=0
            )
            candles.append(
                HourlyOhlcv(
                    symbol=symbol.upper(),
                    hour=hour,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    source="binance",
                )
            )
        if len(batch) < 1000:
            break
        start_ms = int(batch[-1][0]) + MS_PER_HOUR
    return candles


async def fetch_coinbase_daily(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    product_id: str,
    start: date | None,
    end: date,
) -> list[DailyOhlcv]:
    start_dt = datetime.combine(start or date(2015, 1, 1), datetime.min.time(), UTC)
    end_dt = datetime.combine(end, datetime.max.time(), UTC)
    raw = await _fetch_coinbase_raw(
        client,
        product_id=product_id,
        granularity="ONE_DAY",
        start=start_dt,
        end=end_dt,
        chunk_days=COINBASE_DAILY_CHUNK_DAYS,
    )
    by_day = {
        datetime.fromtimestamp(int(row["start"]), tz=UTC).date(): DailyOhlcv(
            symbol=symbol.upper(),
            date=datetime.fromtimestamp(int(row["start"]), tz=UTC).date(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            source="coinbase",
        )
        for row in raw
    }
    return [by_day[d] for d in sorted(by_day)]


async def fetch_coinbase_hourly(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    product_id: str,
    start: datetime,
    end: datetime,
) -> list[HourlyOhlcv]:
    raw = await _fetch_coinbase_raw(
        client,
        product_id=product_id,
        granularity="ONE_HOUR",
        start=start,
        end=end,
        chunk_days=COINBASE_HOURLY_CHUNK_DAYS,
    )
    by_hour = {
        datetime.fromtimestamp(int(row["start"]), tz=UTC).replace(
            minute=0, second=0, microsecond=0
        ): HourlyOhlcv(
            symbol=symbol.upper(),
            hour=datetime.fromtimestamp(int(row["start"]), tz=UTC).replace(
                minute=0, second=0, microsecond=0
            ),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            source="coinbase",
        )
        for row in raw
    }
    return [by_hour[h] for h in sorted(by_hour)]


async def fetch_coingecko_daily(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    coingecko_id: str,
    start: date | None,
    end: date,
) -> list[DailyOhlcv]:
    range_start = start or date(2013, 1, 1)
    candles: list[DailyOhlcv] = []
    chunk_start = range_start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=COINGECKO_CHUNK_DAYS - 1), end)
        resp = await client.get(
            f"{COINGECKO_API}/coins/{coingecko_id}/market_chart/range",
            params={
                "vs_currency": "usd",
                "from": str(_date_seconds(chunk_start, end_of_day=False)),
                "to": str(_date_seconds(chunk_end, end_of_day=True)),
            },
        )
        if resp.status_code == 429:
            await asyncio.sleep(60)
            continue
        if resp.status_code in {401, 404}:
            return candles
        resp.raise_for_status()
        data = resp.json()
        candles.extend(
            _aggregate_market_chart(
                symbol=symbol,
                prices=data.get("prices", []),
                volumes=data.get("total_volumes", []),
            )
        )
        chunk_start = chunk_end + timedelta(days=1)
    by_day = {c.date: c for c in candles}
    return [by_day[d] for d in sorted(by_day)]


async def _fetch_coinbase_raw(
    client: httpx.AsyncClient,
    *,
    product_id: str,
    granularity: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    chunk_end = end
    while chunk_end >= start:
        chunk_start = max(start, chunk_end - timedelta(days=chunk_days - 1))
        resp = await client.get(
            f"{COINBASE_API}/api/v3/brokerage/market/products/{product_id}/candles",
            params={
                "start": str(int(chunk_start.timestamp())),
                "end": str(int(chunk_end.timestamp())),
                "granularity": granularity,
            },
        )
        if resp.status_code in {400, 404}:
            break
        resp.raise_for_status()
        batch = resp.json().get("candles", [])
        if not batch:
            break
        rows.extend(batch)
        chunk_end = chunk_start - timedelta(seconds=1)
    return rows


def _aggregate_market_chart(
    *,
    symbol: str,
    prices: list[list[float]],
    volumes: list[list[float]],
) -> list[DailyOhlcv]:
    volume_by_day: dict[date, float] = {}
    for point in volumes:
        day = datetime.fromtimestamp(point[0] / 1000, tz=UTC).date()
        volume_by_day[day] = float(point[1])

    buckets: dict[date, list[float]] = {}
    for point in prices:
        day = datetime.fromtimestamp(point[0] / 1000, tz=UTC).date()
        buckets.setdefault(day, []).append(float(point[1]))

    return [
        DailyOhlcv(
            symbol=symbol.upper(),
            date=day,
            open=values[0],
            high=max(values),
            low=min(values),
            close=values[-1],
            volume=volume_by_day.get(day, 0.0),
            source="coingecko",
        )
        for day, values in sorted(buckets.items())
    ]


def _date_seconds(day: date, *, end_of_day: bool) -> int:
    clock = datetime.max.time() if end_of_day else datetime.min.time()
    return int(datetime.combine(day, clock, UTC).timestamp())


def _date_ms(day: date, *, end_of_day: bool) -> int:
    return _date_seconds(day, end_of_day=end_of_day) * 1000


def _oi_period(interval: str) -> str:
    if interval == "1h":
        return "1h"
    return "1d"


def _oi_interval_from_period(period: str) -> str:
    if period == "1h":
        return "1h"
    return "1d"


async def fetch_binance_oi(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    pair: str,
    interval: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[OpenInterest]:
    period = _oi_period(interval)
    points: list[OpenInterest] = []
    start_ms = int(start.timestamp() * 1000) if start else None
    end_ms = int(end.timestamp() * 1000) if end else None
    while True:
        params: dict[str, str | int] = {
            "symbol": pair.upper(),
            "period": period,
            "limit": 500,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        resp = await client.get(
            f"{BINANCE_FAPI}/futures/data/openInterestHist",
            params=params,
        )
        if resp.status_code == 400:
            return []
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for row in batch:
            ts = datetime.fromtimestamp(
                int(row["timestamp"]) / 1000, tz=UTC
            ).replace(minute=0, second=0, microsecond=0)
            points.append(
                OpenInterest(
                    symbol=symbol.upper(),
                    timestamp=ts,
                    open_interest=float(row["sumOpenInterestValue"]),
                    exchange="binance",
                    unit="usd_notional",
                    interval=interval,
                )
            )
        if len(batch) < 500:
            break
        start_ms = int(batch[-1]["timestamp"]) + MS_PER_HOUR
    return points


def _bybit_interval(interval: str) -> str:
    return "1h" if interval == "1h" else "1d"


async def fetch_bybit_oi(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    pair: str,
    interval: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[OpenInterest]:
    interval_time = _bybit_interval(interval)
    points: list[OpenInterest] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {
            "category": "linear",
            "symbol": pair.upper(),
            "intervalTime": interval_time,
            "limit": 200,
        }
        if start is not None:
            params["startTime"] = int(start.timestamp() * 1000)
        if end is not None:
            params["endTime"] = int(end.timestamp() * 1000)
        if cursor:
            params["cursor"] = cursor
        resp = await client.get(
            f"{BYBIT_API}/v5/market/open-interest",
            params=params,
        )
        if resp.status_code in {400, 403}:
            return []
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return []
        result = data.get("result", {})
        batch = result.get("list", [])
        if not batch:
            break
        for row in batch:
            ts = datetime.fromtimestamp(
                int(row["timestamp"]) / 1000, tz=UTC
            ).replace(minute=0, second=0, microsecond=0)
            points.append(
                OpenInterest(
                    symbol=symbol.upper(),
                    timestamp=ts,
                    open_interest=float(row["openInterest"]),
                    exchange="bybit",
                    unit="coin",
                    interval=interval,
                )
            )
        cursor = result.get("nextPageCursor")
        if not cursor or len(batch) < 200:
            break
    return points


def okx_inst_id(symbol: str) -> str:
    return f"{symbol.upper()}-USDT-SWAP"


def _okx_bar(interval: str) -> str:
    return "1H" if interval == "1h" else "1D"


async def fetch_okx_oi(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    interval: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[OpenInterest]:
    inst_id = okx_inst_id(symbol)
    bar = _okx_bar(interval)
    points: list[OpenInterest] = []
    after_ms: int | None = (
        int(end.timestamp() * 1000) if end else None
    )
    while True:
        params: dict[str, str | int] = {
            "instId": inst_id,
            "bar": bar,
            "limit": 100,
        }
        if after_ms is not None:
            params["after"] = after_ms
        if start is not None:
            params["before"] = int(start.timestamp() * 1000)
        resp = await client.get(
            f"{OKX_API}/api/v5/market/history-open-interest",
            params=params,
        )
        if resp.status_code in {400, 403}:
            return []
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            return []
        batch = data.get("data", [])
        if not batch:
            break
        for row in batch:
            ts = datetime.fromtimestamp(
                int(row["ts"]) / 1000, tz=UTC
            ).replace(minute=0, second=0, microsecond=0)
            oi_value = float(row.get("oiCcy") or row.get("oi") or 0)
            points.append(
                OpenInterest(
                    symbol=symbol.upper(),
                    timestamp=ts,
                    open_interest=oi_value,
                    exchange="okx",
                    unit="coin",
                    interval=interval,
                )
            )
        if len(batch) < 100:
            break
        after_ms = int(batch[-1]["ts"])
    return points


async def fetch_fred_series(
    client: httpx.AsyncClient,
    *,
    series_id: str,
    api_key: str,
    start: date | None = None,
    end: date | None = None,
) -> list[MacroPoint]:
    params: dict[str, str] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    if start is not None:
        params["observation_start"] = start.isoformat()
    if end is not None:
        params["observation_end"] = end.isoformat()
    resp = await client.get(
        f"{FRED_API}/fred/series/observations",
        params=params,
    )
    resp.raise_for_status()
    data = resp.json()
    observations = data.get("observations", [])
    points: list[MacroPoint] = []
    for obs in observations:
        raw_value = obs.get("value", ".")
        if raw_value in {".", "", None}:
            continue
        try:
            value = float(raw_value)
        except (ValueError, TypeError):
            continue
        obs_date = date.fromisoformat(obs["date"])
        points.append(
            MacroPoint(
                series_id=series_id,
                date=obs_date,
                value=value,
                source="fred",
            )
        )
    return points

