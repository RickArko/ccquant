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


@dataclass(frozen=True)
class WalletRegistryEntry:
    address: str
    chain: str
    label: str
    entity_type: str
    confidence: float
    source: str
    discovered_at: datetime
    active: bool
    metadata_json: str = "{}"


@dataclass(frozen=True)
class WalletTransfer:
    chain: str
    tx_hash: str
    transfer_index: int
    block_time: datetime
    from_address: str
    to_address: str
    asset_mint_or_contract: str
    asset_symbol: str | None
    amount: float
    amount_usd: float | None
    direction: str
    program_or_method: str | None
    source: str


@dataclass(frozen=True)
class WalletPositionDaily:
    address: str
    chain: str
    date: date
    asset_mint: str
    balance: float
    balance_usd: float | None
    source: str


@dataclass
class WalletSyncState:
    address: str
    chain: str
    source: str
    backfill_complete: bool = False
    earliest_at: datetime | None = None
    latest_at: datetime | None = None
    last_refresh_at: datetime | None = None


@dataclass(frozen=True)
class WalletSignalDaily:
    date: date
    chain: str
    smart_money_netflow_usd: float
    kol_buy_count: int
    deployer_activity_count: int
    cabal_alert_count: int
    top_wallet_accumulation_score: float


@dataclass(frozen=True)
class WalletAlert:
    address: str
    chain: str
    mint_or_contract: str
    action: str
    severity: str
    block_time: datetime
    tx_hash: str
    alerted_at: datetime
    metadata_json: str = "{}"

