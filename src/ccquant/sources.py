from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx

from ccquant.models import (
    DailyOhlcv,
    DexPriceDaily,
    HourlyOhlcv,
    MacroPoint,
    OpenInterest,
    OrderBookSnapshot,
)

BINANCE_API = "https://api.binance.com"
BINANCE_FAPI = "https://fapi.binance.com"
BYBIT_API = "https://api.bybit.com"
OKX_API = "https://www.okx.com"
COINBASE_API = "https://api.coinbase.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"
FRED_API = "https://api.stlouisfed.org"
DEFILLAMA_COINS_API = "https://coins.llama.fi"
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
        step_ms = MS_PER_HOUR if period == "1h" else MS_PER_DAY
        start_ms = int(batch[-1]["timestamp"]) + step_ms
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


def extract_depth_features(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    bands: tuple[int, ...] = (10, 25, 50),
) -> dict[str, float | int | None]:
    """Compute mid/spread/bps-band notionals from a limit book.

    ``bids`` / ``asks`` are ``(price, qty)`` levels. Returns empty dict if
    either side is empty.
    """
    if not bids or not asks:
        return {}
    best_bid = max(price for price, _ in bids)
    best_ask = min(price for price, _ in asks)
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return {}
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 10_000.0

    def _band_notional(
        levels: list[tuple[float, float]], *, side: str, band_bps: int
    ) -> float:
        width = mid * (band_bps / 10_000.0)
        total = 0.0
        for price, qty in levels:
            if side == "bid":
                if price < mid - width:
                    continue
            else:
                if price > mid + width:
                    continue
            total += price * qty
        return total

    features: dict[str, float | int | None] = {
        "mid": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": spread_bps,
        "depth_levels": len(bids) + len(asks),
    }
    band_vals: dict[int, tuple[float, float]] = {}
    for band in bands:
        bid_n = _band_notional(bids, side="bid", band_bps=band)
        ask_n = _band_notional(asks, side="ask", band_bps=band)
        band_vals[band] = (bid_n, ask_n)
        features[f"bid_notional_bps_{band}"] = bid_n
        features[f"ask_notional_bps_{band}"] = ask_n
    bid_25, ask_25 = band_vals.get(25, (0.0, 0.0))
    denom = bid_25 + ask_25
    features["imbalance_bps_25"] = (
        (bid_25 - ask_25) / denom if denom > 0 else None
    )
    return features


def _parse_levels(raw: list[list[str] | list[float]]) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    for row in raw:
        if len(row) < 2:
            continue
        levels.append((float(row[0]), float(row[1])))
    return levels


def _snapshot_from_features(
    *,
    symbol: str,
    exchange: str,
    features: dict[str, float | int | None],
    timestamp: datetime,
    fetched_at: datetime,
    last_update_id: int | None,
) -> OrderBookSnapshot | None:
    if not features:
        return None
    imbalance = features.get("imbalance_bps_25")
    return OrderBookSnapshot(
        symbol=symbol.upper(),
        timestamp=timestamp,
        exchange=exchange,
        mid=float(features["mid"] or 0.0),
        best_bid=float(features["best_bid"] or 0.0),
        best_ask=float(features["best_ask"] or 0.0),
        spread_bps=float(features["spread_bps"] or 0.0),
        bid_notional_bps_10=float(features.get("bid_notional_bps_10") or 0.0),
        ask_notional_bps_10=float(features.get("ask_notional_bps_10") or 0.0),
        bid_notional_bps_25=float(features.get("bid_notional_bps_25") or 0.0),
        ask_notional_bps_25=float(features.get("ask_notional_bps_25") or 0.0),
        bid_notional_bps_50=float(features.get("bid_notional_bps_50") or 0.0),
        ask_notional_bps_50=float(features.get("ask_notional_bps_50") or 0.0),
        imbalance_bps_25=float(imbalance) if imbalance is not None else None,
        depth_levels=int(features.get("depth_levels") or 0),
        last_update_id=last_update_id,
        fetched_at=fetched_at,
    )


async def fetch_binance_depth(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    pair: str,
    limit: int = 100,
) -> OrderBookSnapshot | None:
    resp = await client.get(
        f"{BINANCE_API}/api/v3/depth",
        params={"symbol": pair.upper(), "limit": limit},
    )
    if resp.status_code in {400, 403, 404}:
        return None
    resp.raise_for_status()
    data = resp.json()
    bids = _parse_levels(data.get("bids", []))
    asks = _parse_levels(data.get("asks", []))
    now = datetime.now(tz=UTC).replace(microsecond=0)
    features = extract_depth_features(bids, asks)
    last_update_id = data.get("lastUpdateId")
    return _snapshot_from_features(
        symbol=symbol,
        exchange="binance",
        features=features,
        timestamp=now,
        fetched_at=now,
        last_update_id=int(last_update_id) if last_update_id is not None else None,
    )


async def fetch_bybit_depth(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    pair: str,
    limit: int = 50,
) -> OrderBookSnapshot | None:
    resp = await client.get(
        f"{BYBIT_API}/v5/market/orderbook",
        params={
            "category": "spot",
            "symbol": pair.upper(),
            "limit": min(limit, 200),
        },
    )
    if resp.status_code in {400, 403, 404}:
        return None
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("retCode") != 0:
        return None
    result = payload.get("result", {})
    bids = _parse_levels(result.get("b", []))
    asks = _parse_levels(result.get("a", []))
    ts_ms = result.get("ts")
    now = datetime.now(tz=UTC).replace(microsecond=0)
    timestamp = (
        datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).replace(microsecond=0)
        if ts_ms is not None
        else now
    )
    features = extract_depth_features(bids, asks)
    update_id = result.get("u")
    return _snapshot_from_features(
        symbol=symbol,
        exchange="bybit",
        features=features,
        timestamp=timestamp,
        fetched_at=now,
        last_update_id=int(update_id) if update_id is not None else None,
    )


def okx_spot_inst_id(symbol: str) -> str:
    return f"{symbol.upper()}-USDT"


async def fetch_okx_depth(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    limit: int = 50,
) -> OrderBookSnapshot | None:
    resp = await client.get(
        f"{OKX_API}/api/v5/market/books",
        params={"instId": okx_spot_inst_id(symbol), "sz": min(limit, 400)},
    )
    if resp.status_code in {400, 403, 404}:
        return None
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != "0":
        return None
    batch = payload.get("data", [])
    if not batch:
        return None
    book = batch[0]
    bids = _parse_levels(book.get("bids", []))
    asks = _parse_levels(book.get("asks", []))
    ts_ms = book.get("ts")
    now = datetime.now(tz=UTC).replace(microsecond=0)
    timestamp = (
        datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).replace(microsecond=0)
        if ts_ms is not None
        else now
    )
    features = extract_depth_features(bids, asks)
    seq = book.get("seqId")
    return _snapshot_from_features(
        symbol=symbol,
        exchange="okx",
        features=features,
        timestamp=timestamp,
        fetched_at=now,
        last_update_id=int(seq) if seq is not None else None,
    )


async def fetch_defillama_prices(
    client: httpx.AsyncClient,
    *,
    symbol_to_coingecko_id: dict[str, str],
) -> list[DexPriceDaily]:
    """Fetch current USD prices from DefiLlama coins API (keyless)."""
    if not symbol_to_coingecko_id:
        return []
    keys = [
        f"coingecko:{cg_id}"
        for cg_id in symbol_to_coingecko_id.values()
    ]
    resp = await client.get(
        f"{DEFILLAMA_COINS_API}/prices/current/{','.join(keys)}"
    )
    if resp.status_code in {400, 403, 404}:
        return []
    resp.raise_for_status()
    coins = resp.json().get("coins", {})
    cg_to_symbol = {
        cg_id: symbol.upper()
        for symbol, cg_id in symbol_to_coingecko_id.items()
    }
    points: list[DexPriceDaily] = []
    today = date.today()
    for key, payload in coins.items():
        # key like "coingecko:bitcoin"
        cg_id = key.split(":", 1)[-1]
        symbol = cg_to_symbol.get(cg_id)
        if symbol is None:
            continue
        price = payload.get("price")
        if price is None:
            continue
        ts = payload.get("timestamp")
        price_date = (
            datetime.fromtimestamp(int(ts), tz=UTC).date()
            if ts is not None
            else today
        )
        points.append(
            DexPriceDaily(
                symbol=symbol,
                date=price_date,
                venue="defillama",
                price_usd=float(price),
                source="defillama",
            )
        )
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

