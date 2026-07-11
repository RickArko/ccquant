from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class DailyConfig:
    tail_days: int = 7


@dataclass(frozen=True)
class HourlyConfig:
    enabled: bool = True
    top: int = 10
    history_days: int = 365
    tail_hours: int = 168


@dataclass(frozen=True)
class OpenInterestConfig:
    enabled: bool = True
    history_days: int = 365
    tail_hours: int = 168
    request_delay_seconds: float = 0.25
    binance: bool = True
    bybit: bool = True
    okx: bool = True


FRED_SERIES: list[str] = [
    "M2SL",
    "WALCL",
    "DGS10",
    "DGS2",
    "T10YIE",
    "FEDFUNDS",
    "DTWEXBGS",
    "VIXCLS",
]


@dataclass(frozen=True)
class MacroConfig:
    enabled: bool = True
    series_ids: list[str] = field(default_factory=lambda: list(FRED_SERIES))
    request_delay_seconds: float = 1.0


@dataclass(frozen=True)
class UniverseConfig:
    size: int = 100
    include_symbols: list[str] = field(default_factory=list)
    source_preference: str = "binance"
    request_delay_seconds: float = 0.25


@dataclass(frozen=True)
class WalletHistoryConfig:
    solana_source: str = "solarchive"
    bitcoin_source: str = "bigquery"
    extract_days: int = 7


@dataclass(frozen=True)
class WalletTailConfig:
    enabled: bool = True
    interval_minutes: int = 15
    max_wallets: int = 50
    request_delay_seconds: float = 1.0
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    bitcoin_api_url: str = "https://mempool.space/api"


@dataclass(frozen=True)
class WalletDiscoveryConfig:
    flipside_enabled: bool = True
    min_win_rate: float = 0.35
    request_delay_seconds: float = 1.0


@dataclass(frozen=True)
class WalletTrackingConfig:
    enabled: bool = True
    chains: list[str] = field(
        default_factory=lambda: ["solana", "arbitrum", "bitcoin"]
    )
    seed_file: Path = field(
        default_factory=lambda: Path("config/seeds/wallet_registry_seed.csv")
    )
    identities_seed_file: Path = field(
        default_factory=lambda: Path("config/seeds/wallet_identities_seed.csv")
    )
    identity_links_seed_file: Path = field(
        default_factory=lambda: Path(
            "config/seeds/wallet_identity_links_seed.csv"
        )
    )
    history: WalletHistoryConfig = field(default_factory=WalletHistoryConfig)
    tail: WalletTailConfig = field(default_factory=WalletTailConfig)
    discovery: WalletDiscoveryConfig = field(
        default_factory=WalletDiscoveryConfig
    )


@dataclass(frozen=True)
class TwitterImportConfig:
    inbox_dir: Path = field(
        default_factory=lambda: Path("data/twitter/inbox")
    )
    archive_dir: Path = field(
        default_factory=lambda: Path("data/twitter/archive")
    )
    formats: list[str] = field(default_factory=lambda: ["csv", "jsonl"])
    on_conflict: str = "skip"


@dataclass(frozen=True)
class TwitterEnrichmentConfig:
    extract_cashtags: bool = True
    extract_addresses: bool = True
    extract_sol_domains: bool = True
    keyword_sentiment: bool = True
    bullish_keywords: list[str] = field(
        default_factory=lambda: [
            "long",
            "buy",
            "bullish",
            "accumulate",
            "pump",
            "moon",
            "breakout",
        ]
    )
    bearish_keywords: list[str] = field(
        default_factory=lambda: [
            "short",
            "sell",
            "bearish",
            "dump",
            "rug",
            "crash",
            "rekt",
        ]
    )


@dataclass(frozen=True)
class TwitterSignalsConfig:
    spike_window_days: int = 30
    spike_z_threshold: float = 2.0


@dataclass(frozen=True)
class TwitterTrackingConfig:
    enabled: bool = True
    accounts_seed: Path = field(
        default_factory=lambda: Path("config/seeds/twitter_accounts_seed.csv")
    )
    import_config: TwitterImportConfig = field(
        default_factory=TwitterImportConfig
    )
    enrichment: TwitterEnrichmentConfig = field(
        default_factory=TwitterEnrichmentConfig
    )
    signals: TwitterSignalsConfig = field(default_factory=TwitterSignalsConfig)


@dataclass(frozen=True)
class AppConfig:
    database: Path
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    hourly: HourlyConfig = field(default_factory=HourlyConfig)
    open_interest: OpenInterestConfig = field(
        default_factory=OpenInterestConfig
    )
    macro: MacroConfig = field(default_factory=MacroConfig)
    wallet_tracking: WalletTrackingConfig = field(
        default_factory=WalletTrackingConfig
    )
    twitter_tracking: TwitterTrackingConfig = field(
        default_factory=TwitterTrackingConfig
    )


def load_config(path: str | Path | None = None) -> AppConfig:
    data: dict[str, Any] = {}
    if path is not None:
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
            if not isinstance(raw, dict):
                raise ValueError("config root must be a mapping")
            data = raw

    database = Path(
        os.environ.get("CCQUANT_DB")
        or os.path.expandvars(str(data.get("database", "data/ccquant.duckdb")))
    )
    universe_data = data.get("universe", {}) or {}
    daily_data = data.get("daily", {}) or {}
    hourly_data = data.get("hourly", {}) or {}
    oi_data = data.get("open_interest", {}) or {}
    macro_data = data.get("macro", {}) or {}
    wallet_data = data.get("wallet_tracking", {}) or {}
    twitter_data = data.get("twitter_tracking", {}) or {}
    history_data = wallet_data.get("history", {}) or {}
    tail_data = wallet_data.get("tail", {}) or {}
    discovery_data = wallet_data.get("discovery", {}) or {}
    twitter_import_data = twitter_data.get("import", {}) or {}
    twitter_enrich_data = twitter_data.get("enrichment", {}) or {}
    twitter_signals_data = twitter_data.get("signals", {}) or {}
    return AppConfig(
        database=database,
        universe=UniverseConfig(
            size=int(universe_data.get("size", 100)),
            include_symbols=[
                str(symbol).upper()
                for symbol in universe_data.get("include_symbols", [])
            ],
            source_preference=str(universe_data.get("source_preference", "binance")),
            request_delay_seconds=float(
                universe_data.get("request_delay_seconds", 0.25)
            ),
        ),
        daily=DailyConfig(tail_days=int(daily_data.get("tail_days", 7))),
        hourly=HourlyConfig(
            enabled=bool(hourly_data.get("enabled", True)),
            top=int(hourly_data.get("top", 10)),
            history_days=int(hourly_data.get("history_days", 365)),
            tail_hours=int(hourly_data.get("tail_hours", 168)),
        ),
        open_interest=OpenInterestConfig(
            enabled=bool(oi_data.get("enabled", True)),
            history_days=int(oi_data.get("history_days", 365)),
            tail_hours=int(oi_data.get("tail_hours", 168)),
            request_delay_seconds=float(
                oi_data.get("request_delay_seconds", 0.25)
            ),
            binance=bool(oi_data.get("binance", True)),
            bybit=bool(oi_data.get("bybit", True)),
            okx=bool(oi_data.get("okx", True)),
        ),
        macro=MacroConfig(
            enabled=bool(macro_data.get("enabled", True)),
            series_ids=[
                str(sid) for sid in macro_data.get("series_ids", FRED_SERIES)
            ],
            request_delay_seconds=float(
                macro_data.get("request_delay_seconds", 1.0)
            ),
        ),
        wallet_tracking=WalletTrackingConfig(
            enabled=bool(wallet_data.get("enabled", True)),
            chains=[
                str(chain).lower()
                for chain in wallet_data.get(
                    "chains", ["solana", "arbitrum", "bitcoin"]
                )
            ],
            seed_file=Path(
                str(
                    wallet_data.get(
                        "seed_file", "config/seeds/wallet_registry_seed.csv"
                    )
                )
            ),
            identities_seed_file=Path(
                str(
                    wallet_data.get(
                        "identities_seed_file",
                        "config/seeds/wallet_identities_seed.csv",
                    )
                )
            ),
            identity_links_seed_file=Path(
                str(
                    wallet_data.get(
                        "identity_links_seed_file",
                        "config/seeds/wallet_identity_links_seed.csv",
                    )
                )
            ),
            history=WalletHistoryConfig(
                solana_source=str(
                    history_data.get("solana_source", "solarchive")
                ),
                bitcoin_source=str(
                    history_data.get("bitcoin_source", "bigquery")
                ),
                extract_days=int(history_data.get("extract_days", 7)),
            ),
            tail=WalletTailConfig(
                enabled=bool(tail_data.get("enabled", True)),
                interval_minutes=int(tail_data.get("interval_minutes", 15)),
                max_wallets=int(tail_data.get("max_wallets", 50)),
                request_delay_seconds=float(
                    tail_data.get("request_delay_seconds", 1.0)
                ),
                solana_rpc_url=str(
                    tail_data.get(
                        "solana_rpc_url",
                        "https://api.mainnet-beta.solana.com",
                    )
                ),
                bitcoin_api_url=str(
                    tail_data.get(
                        "bitcoin_api_url",
                        "https://mempool.space/api",
                    )
                ),
            ),
            discovery=WalletDiscoveryConfig(
                flipside_enabled=bool(
                    discovery_data.get("flipside_enabled", True)
                ),
                min_win_rate=float(discovery_data.get("min_win_rate", 0.35)),
                request_delay_seconds=float(
                    discovery_data.get("request_delay_seconds", 1.0)
                ),
            ),
        ),
        twitter_tracking=TwitterTrackingConfig(
            enabled=bool(twitter_data.get("enabled", True)),
            accounts_seed=Path(
                str(
                    twitter_data.get(
                        "accounts_seed",
                        "config/seeds/twitter_accounts_seed.csv",
                    )
                )
            ),
            import_config=TwitterImportConfig(
                inbox_dir=Path(
                    str(
                        twitter_import_data.get(
                            "inbox_dir", "data/twitter/inbox"
                        )
                    )
                ),
                archive_dir=Path(
                    str(
                        twitter_import_data.get(
                            "archive_dir", "data/twitter/archive"
                        )
                    )
                ),
                formats=[
                    str(fmt).lower()
                    for fmt in twitter_import_data.get(
                        "formats", ["csv", "jsonl"]
                    )
                ],
                on_conflict=str(
                    twitter_import_data.get("on_conflict", "skip")
                ),
            ),
            enrichment=TwitterEnrichmentConfig(
                extract_cashtags=bool(
                    twitter_enrich_data.get("extract_cashtags", True)
                ),
                extract_addresses=bool(
                    twitter_enrich_data.get("extract_addresses", True)
                ),
                extract_sol_domains=bool(
                    twitter_enrich_data.get("extract_sol_domains", True)
                ),
                keyword_sentiment=bool(
                    twitter_enrich_data.get("keyword_sentiment", True)
                ),
                bullish_keywords=[
                    str(kw).lower()
                    for kw in twitter_enrich_data.get(
                        "bullish_keywords",
                        TwitterEnrichmentConfig().bullish_keywords,
                    )
                ],
                bearish_keywords=[
                    str(kw).lower()
                    for kw in twitter_enrich_data.get(
                        "bearish_keywords",
                        TwitterEnrichmentConfig().bearish_keywords,
                    )
                ],
            ),
            signals=TwitterSignalsConfig(
                spike_window_days=int(
                    twitter_signals_data.get("spike_window_days", 30)
                ),
                spike_z_threshold=float(
                    twitter_signals_data.get("spike_z_threshold", 2.0)
                ),
            ),
        ),
    )
