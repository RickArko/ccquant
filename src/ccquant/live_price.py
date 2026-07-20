"""Near-live BTC price tape for the Market Tracker dashboard.

Fetches Binance spot last/24h stats + short-interval klines at render time.
Falls back to Coinbase public spot if Binance is unreachable (e.g. geo-block).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

LOGGER = logging.getLogger(__name__)

BINANCE_API = "https://api.binance.com"
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"

LiveInterval = Literal["1m", "5m", "15m"]

_INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000}
_COINBASE_GRANULARITY = {"1m": 60, "5m": 300, "15m": 900}


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
    bar_times: tuple[datetime, ...]
    bar_closes: tuple[float, ...]


def _parse_binance_klines(
    rows: list[list[object]],
) -> tuple[tuple[datetime, ...], tuple[float, ...]]:
    times: list[datetime] = []
    closes: list[float] = []
    for row in rows:
        times.append(datetime.fromtimestamp(int(str(row[0])) / 1000, tz=UTC))
        closes.append(float(str(row[4])))
    return tuple(times), tuple(closes)


def fetch_live_tape(
    *,
    interval: LiveInterval = "5m",
    limit: int = 72,
    client: httpx.Client | None = None,
) -> LiveTape:
    """Fetch near-live BTC last price + short bars (Binance → Coinbase)."""
    if interval not in _INTERVAL_MS:
        raise ValueError(f"unsupported interval {interval!r}")

    own = client is None
    http = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        try:
            return _fetch_binance(http, interval=interval, limit=limit)
        except Exception as exc:
            LOGGER.warning("Binance live tape failed (%s); trying Coinbase", exc)
            return _fetch_coinbase(http, interval=interval, limit=limit)
    finally:
        if own:
            http.close()


def _fetch_binance(
    client: httpx.Client,
    *,
    interval: LiveInterval,
    limit: int,
) -> LiveTape:
    ticker = client.get(
        f"{BINANCE_API}/api/v3/ticker/24hr",
        params={"symbol": "BTCUSDT"},
    )
    ticker.raise_for_status()
    t = ticker.json()
    last = float(t["lastPrice"])
    change = float(t["priceChangePercent"]) / 100.0
    high = float(t["highPrice"])
    low = float(t["lowPrice"])
    as_of = datetime.fromtimestamp(int(t["closeTime"]) / 1000, tz=UTC)

    kl = client.get(
        f"{BINANCE_API}/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
    )
    kl.raise_for_status()
    times, closes = _parse_binance_klines(kl.json())
    return LiveTape(
        last=last,
        change_24h_pct=change,
        high_24h=high,
        low_24h=low,
        as_of=as_of,
        source="binance",
        interval=interval,
        bar_times=times,
        bar_closes=closes,
    )


def _fetch_coinbase(
    client: httpx.Client,
    *,
    interval: LiveInterval,
    limit: int,
) -> LiveTape:
    spot = client.get(COINBASE_SPOT)
    spot.raise_for_status()
    last = float(spot.json()["data"]["amount"])

    gran = _COINBASE_GRANULARITY[interval]
    candles = client.get(
        COINBASE_CANDLES,
        params={"granularity": gran},
        headers={"User-Agent": "ccquant/0.1"},
    )
    candles.raise_for_status()
    # Coinbase returns [time, low, high, open, close, volume], newest first.
    rows = list(reversed(candles.json()[:limit]))
    times = tuple(datetime.fromtimestamp(int(r[0]), tz=UTC) for r in rows)
    closes = tuple(float(r[4]) for r in rows)
    as_of = times[-1] if times else datetime.now(tz=UTC)
    return LiveTape(
        last=last,
        change_24h_pct=None,
        high_24h=None,
        low_24h=None,
        as_of=as_of,
        source="coinbase",
        interval=interval,
        bar_times=times,
        bar_closes=closes,
    )
