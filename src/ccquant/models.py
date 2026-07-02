from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Asset:
    rank: int
    symbol: str
    coingecko_id: str
    binance_pair: str | None
    coinbase_product_id: str | None
    active: bool
    as_of_date: date


@dataclass(frozen=True)
class DailyOhlcv:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str


@dataclass(frozen=True)
class HourlyOhlcv:
    symbol: str
    hour: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str


@dataclass
class SyncState:
    symbol: str
    interval: str
    backfill_complete: bool = False
    earliest_at: date | datetime | None = None
    latest_at: date | datetime | None = None
    last_refresh_at: datetime | None = None

