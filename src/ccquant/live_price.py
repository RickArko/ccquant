"""Near-live BTC price tape for the Market Tracker dashboard.

Fetches Binance spot last/24h stats + short-interval OHLC klines at render
time (tries public data-api host first — api.binance.com is often geo-blocked).
Falls back to Coinbase public spot/candles if Binance is unreachable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx

LOGGER = logging.getLogger(__name__)

BINANCE_HOSTS = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us",
)
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"

LiveInterval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]
LiveRange = Literal["1h", "1d", "7d"]

_INTERVAL_SEC = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3_600,
    "4h": 14_400,
    "1d": 86_400,
}
_RANGE_SEC = {"1h": 3_600, "1d": 86_400, "7d": 604_800}
# Coinbase has no 4h bucket — 6h (21600) is the closest public granularity.
_COINBASE_GRANULARITY = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3_600,
    "4h": 21_600,
    "1d": 86_400,
}
# Interval buttons offered per chart window (short tape vs multi-day).
INTERVALS_FOR_RANGE: dict[LiveRange, tuple[LiveInterval, ...]] = {
    "1h": ("1m", "5m", "15m", "1h"),
    "1d": ("1h", "4h"),
    "7d": ("4h", "1d"),
}
DEFAULT_INTERVAL_FOR_RANGE: dict[LiveRange, LiveInterval] = {
    "1h": "5m",
    "1d": "4h",
    "7d": "4h",
}
BINANCE_PAGE = 1000
COINBASE_PAGE = 300


@dataclass(frozen=True)
class LiveTape:
    """Compact near-live BTC tape for the dashboard hero."""

    last: float
    change_24h_pct: float | None
    high_24h: float | None
    low_24h: float | None
    as_of: datetime
    source: str
    interval: LiveInterval
    range_key: LiveRange
    bar_times: tuple[datetime, ...]
    bar_opens: tuple[float, ...]
    bar_highs: tuple[float, ...]
    bar_lows: tuple[float, ...]
    bar_closes: tuple[float, ...]


def kline_limit(range_key: LiveRange, interval: LiveInterval) -> int:
    """Bars needed for a range/interval pair (not capped — callers paginate)."""
    if range_key not in _RANGE_SEC or interval not in _INTERVAL_SEC:
        raise ValueError(f"unsupported range/interval {range_key!r}/{interval!r}")
    return max(1, int(_RANGE_SEC[range_key] / _INTERVAL_SEC[interval]))


def _parse_binance_klines(
    rows: list[list[object]],
) -> tuple[
    tuple[datetime, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
]:
    times: list[datetime] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in rows:
        times.append(datetime.fromtimestamp(int(str(row[0])) / 1000, tz=UTC))
        opens.append(float(str(row[1])))
        highs.append(float(str(row[2])))
        lows.append(float(str(row[3])))
        closes.append(float(str(row[4])))
    return (
        tuple(times),
        tuple(opens),
        tuple(highs),
        tuple(lows),
        tuple(closes),
    )


def fetch_live_tape(
    *,
    interval: LiveInterval = "5m",
    range_key: LiveRange = "1h",
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> LiveTape:
    """Fetch near-live BTC last price + OHLC bars (Binance → Coinbase)."""
    if interval not in _INTERVAL_SEC:
        raise ValueError(f"unsupported interval {interval!r}")
    if range_key not in _RANGE_SEC:
        raise ValueError(f"unsupported range {range_key!r}")
    n = limit if limit is not None else kline_limit(range_key, interval)

    own = client is None
    http = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        try:
            return _fetch_binance(
                http, interval=interval, range_key=range_key, limit=n
            )
        except Exception as exc:
            LOGGER.warning("Binance live tape failed (%s); trying Coinbase", exc)
            return _fetch_coinbase(
                http, interval=interval, range_key=range_key, limit=n
            )
    finally:
        if own:
            http.close()


def _fetch_binance_page(
    client: httpx.Client,
    host: str,
    *,
    interval: LiveInterval,
    limit: int,
    end_time_ms: int | None,
) -> list[list[object]]:
    params: dict[str, str | int] = {
        "symbol": "BTCUSDT",
        "interval": interval,
        "limit": limit,
    }
    if end_time_ms is not None:
        params["endTime"] = end_time_ms
    resp = client.get(f"{host}/api/v3/klines", params=params)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        raise TypeError(f"unexpected klines payload: {type(rows)!r}")
    return rows


def _fetch_binance(
    client: httpx.Client,
    *,
    interval: LiveInterval,
    range_key: LiveRange,
    limit: int,
) -> LiveTape:
    last_err: Exception | None = None
    for host in BINANCE_HOSTS:
        try:
            ticker = client.get(
                f"{host}/api/v3/ticker/24hr",
                params={"symbol": "BTCUSDT"},
            )
            ticker.raise_for_status()
            t = ticker.json()
            last = float(t["lastPrice"])
            change = float(t["priceChangePercent"]) / 100.0
            high = float(t["highPrice"])
            low = float(t["lowPrice"])
            as_of = datetime.fromtimestamp(int(t["closeTime"]) / 1000, tz=UTC)

            collected: list[list[object]] = []
            end_time: int | None = None
            while len(collected) < limit:
                page = min(BINANCE_PAGE, limit - len(collected))
                batch = _fetch_binance_page(
                    client,
                    host,
                    interval=interval,
                    limit=page,
                    end_time_ms=end_time,
                )
                if not batch:
                    break
                collected = batch + collected
                end_time = int(str(batch[0][0])) - 1
                if len(batch) < page:
                    break

            if not collected:
                raise RuntimeError(f"empty klines from {host}")

            # Deduplicate + keep the most recent ``limit`` bars.
            by_t: dict[int, list[object]] = {}
            for row in collected:
                by_t[int(str(row[0]))] = row
            ordered = [by_t[k] for k in sorted(by_t)]
            ordered = ordered[-limit:]
            times, opens, highs, lows, closes = _parse_binance_klines(ordered)
            source = host.removeprefix("https://")
            return LiveTape(
                last=last,
                change_24h_pct=change,
                high_24h=high,
                low_24h=low,
                as_of=as_of,
                source=source,
                interval=interval,
                range_key=range_key,
                bar_times=times,
                bar_opens=opens,
                bar_highs=highs,
                bar_lows=lows,
                bar_closes=closes,
            )
        except Exception as exc:
            last_err = exc
            LOGGER.debug("binance host %s failed: %s", host, exc)
            continue
    raise RuntimeError(f"all Binance hosts failed: {last_err}")


def _fetch_coinbase(
    client: httpx.Client,
    *,
    interval: LiveInterval,
    range_key: LiveRange,
    limit: int,
) -> LiveTape:
    spot = client.get(COINBASE_SPOT)
    spot.raise_for_status()
    last = float(spot.json()["data"]["amount"])

    gran = _COINBASE_GRANULARITY[interval]
    collected: list[list[object]] = []
    end = datetime.now(tz=UTC)
    while len(collected) < limit:
        page = min(COINBASE_PAGE, limit - len(collected))
        start = end - timedelta(seconds=page * gran)
        candles = client.get(
            COINBASE_CANDLES,
            params={
                "granularity": gran,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
            },
            headers={"User-Agent": "ccquant/0.1"},
        )
        candles.raise_for_status()
        batch = candles.json()
        if not isinstance(batch, list) or not batch:
            break
        # Newest first from API; normalize oldest→newest for prepend.
        batch_sorted = sorted(batch, key=lambda r: int(r[0]))
        collected = batch_sorted + collected
        end = datetime.fromtimestamp(int(batch_sorted[0][0]), tz=UTC) - timedelta(
            seconds=gran
        )
        if len(batch) < page:
            break

    by_t: dict[int, list[object]] = {}
    for row in collected:
        by_t[int(str(row[0]))] = row
    rows = [by_t[k] for k in sorted(by_t)][-limit:]
    times = tuple(datetime.fromtimestamp(int(str(r[0])), tz=UTC) for r in rows)
    lows = tuple(float(str(r[1])) for r in rows)
    highs = tuple(float(str(r[2])) for r in rows)
    opens = tuple(float(str(r[3])) for r in rows)
    closes = tuple(float(str(r[4])) for r in rows)
    as_of = times[-1] if times else datetime.now(tz=UTC)
    return LiveTape(
        last=last,
        change_24h_pct=None,
        high_24h=None,
        low_24h=None,
        as_of=as_of,
        source="coinbase",
        interval=interval,
        range_key=range_key,
        bar_times=times,
        bar_opens=opens,
        bar_highs=highs,
        bar_lows=lows,
        bar_closes=closes,
    )
