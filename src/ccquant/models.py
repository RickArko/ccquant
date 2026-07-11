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


@dataclass(frozen=True)
class OnchainPoint:
    metric: str
    date: date
    value: float
    source: str


@dataclass(frozen=True)
class OnchainSyncState:
    metric: str
    source: str
    latest_at: str | None
    last_refresh_at: datetime | None


@dataclass(frozen=True)
class OpenInterest:
    symbol: str
    timestamp: datetime
    open_interest: float
    exchange: str
    unit: str
    interval: str


@dataclass(frozen=True)
class MacroPoint:
    series_id: str
    date: date
    value: float
    source: str

