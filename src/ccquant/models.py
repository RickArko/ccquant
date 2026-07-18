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
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    exchange: str
    mid: float
    best_bid: float
    best_ask: float
    spread_bps: float
    bid_notional_bps_10: float
    ask_notional_bps_10: float
    bid_notional_bps_25: float
    ask_notional_bps_25: float
    bid_notional_bps_50: float
    ask_notional_bps_50: float
    imbalance_bps_25: float | None
    depth_levels: int
    last_update_id: int | None
    fetched_at: datetime


@dataclass
class OrderBookSyncState:
    symbol: str
    exchange: str
    latest_at: datetime | None = None
    last_refresh_at: datetime | None = None
    snapshot_count: int = 0


@dataclass(frozen=True)
class DexPriceDaily:
    symbol: str
    date: date
    venue: str
    price_usd: float
    source: str


@dataclass(frozen=True)
class MevBoostPayload:
    slot: int
    block_number: int | None
    builder_pubkey: str | None
    proposer_fee_recipient: str | None
    value_wei: float
    value_eth: float
    relay: str
    date: date
    source: str


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


@dataclass(frozen=True)
class WalletIdentity:
    identity_id: str
    display_name: str
    category: str
    description: str
    source_url: str
    active: bool


@dataclass(frozen=True)
class WalletIdentityLink:
    address: str
    chain: str
    identity_id: str
    link_type: str
    confidence: float
    source: str
    linked_at: datetime


@dataclass(frozen=True)
class TwitterAccount:
    handle: str
    user_id: str | None
    display_name: str
    entity_type: str
    chains: str
    symbols_watch: str
    confidence: float
    source: str
    active: bool
    metadata_json: str = "{}"


@dataclass(frozen=True)
class Tweet:
    tweet_id: str
    handle: str
    posted_at: datetime
    text: str
    lang: str | None
    is_retweet: bool
    is_reply: bool
    reply_to_tweet_id: str | None
    conversation_id: str | None
    like_count: int
    retweet_count: int
    reply_count: int
    import_source: str
    imported_at: datetime
    raw_json: str


@dataclass(frozen=True)
class TweetEntity:
    tweet_id: str
    entity_type: str
    entity_value: str


@dataclass
class TweetSyncState:
    handle: str
    earliest_at: datetime | None = None
    latest_at: datetime | None = None
    latest_tweet_id: str | None = None
    last_import_at: datetime | None = None
    backfill_complete: bool = False


@dataclass(frozen=True)
class TweetSignalDaily:
    date: date
    symbol: str
    mention_count: int
    kol_mention_count: int
    bullish_keyword_count: int
    bearish_keyword_count: int
    unique_accounts: int


@dataclass(frozen=True)
class TweetAlert:
    tweet_id: str
    handle: str
    alert_type: str
    severity: str
    symbols: str
    posted_at: datetime
    alerted_at: datetime
    metadata_json: str = "{}"

