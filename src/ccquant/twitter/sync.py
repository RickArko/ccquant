from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from ccquant.config import AppConfig
from ccquant.models import (
    Tweet,
    TweetEntity,
    TweetSignalDaily,
    TweetSyncState,
    TwitterAccount,
)
from ccquant.storage import MarketStore
from ccquant.twitter.accounts import load_seed_accounts, resolve_accounts_seed
from ccquant.twitter.enrich import (
    extract_entities,
    map_symbol_to_universe,
    score_sentiment,
)
from ccquant.twitter.import_csv import load_tweets_csv
from ccquant.twitter.import_jsonl import load_tweets_jsonl


@dataclass
class _SignalBucket:
    mention_count: int = 0
    kol_mention_count: int = 0
    bullish_keyword_count: int = 0
    bearish_keyword_count: int = 0
    unique_accounts: set[str] = field(default_factory=set)


class TwitterSync:
    def __init__(self, store: MarketStore, config: AppConfig) -> None:
        self.store = store
        self.config = config

    def sync_all(self) -> dict[str, int]:
        cfg = self.config.twitter_tracking
        if not cfg.enabled:
            return {}
        counts: dict[str, int] = {}
        counts["accounts"] = self.load_accounts()
        import_counts = self.import_inbox()
        counts.update(import_counts)
        counts["entities"] = self.enrich_recent_tweets()
        counts["signals"] = self.aggregate_signals()
        alert_count = self.detect_and_store_alerts()
        counts["alerts"] = alert_count
        return counts

    def load_accounts(self) -> int:
        seed_path = resolve_accounts_seed(self.config.twitter_tracking.accounts_seed)
        accounts = load_seed_accounts(seed_path)
        return self.store.upsert_twitter_accounts(accounts)

    def import_file(self, path: Path) -> int:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            tweets = load_tweets_csv(path)
            source = "csv"
        elif suffix in {".jsonl", ".ndjson"}:
            tweets = load_tweets_jsonl(path)
            source = "jsonl"
        else:
            raise ValueError(f"unsupported import format: {path.suffix}")
        return self._persist_import(tweets, source=source)

    def import_inbox(self) -> dict[str, int]:
        cfg = self.config.twitter_tracking.import_config
        cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
        cfg.archive_dir.mkdir(parents=True, exist_ok=True)
        counts = {"imported": 0, "files": 0}
        patterns = []
        if "csv" in cfg.formats:
            patterns.append("*.csv")
        if "jsonl" in cfg.formats:
            patterns.extend(["*.jsonl", "*.ndjson"])
        files: list[Path] = []
        for pattern in patterns:
            files.extend(sorted(cfg.inbox_dir.glob(pattern)))
        for path in files:
            imported = self.import_file(path)
            counts["imported"] += imported
            counts["files"] += 1
            archive_path = cfg.archive_dir / path.name
            if archive_path.exists():
                stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
                archive_path = cfg.archive_dir / f"{stamp}_{path.name}"
            path.rename(archive_path)
        return counts

    def enrich_recent_tweets(self) -> int:
        cfg = self.config.twitter_tracking.enrichment
        tweets = self.store.tweets_needing_enrichment()
        all_entities: list[TweetEntity] = []
        for tweet in tweets:
            all_entities.extend(extract_entities(tweet, cfg))
        if all_entities:
            self.store.upsert_tweet_entities(all_entities)
        return len(all_entities)

    def aggregate_signals(self) -> int:
        cfg = self.config.twitter_tracking
        universe = {asset.symbol for asset in self.store.active_assets()}
        kol_handles = {
            account.handle
            for account in self.store.active_twitter_accounts()
            if account.entity_type == "kol"
        }
        tweets = self.store.all_tweets()
        account_map = {
            account.handle: account
            for account in self.store.all_twitter_accounts()
        }
        buckets: dict[tuple[date, str], _SignalBucket] = {}

        for tweet in tweets:
            bullish, bearish = score_sentiment(tweet.text, cfg.enrichment)
            entities = self.store.tweet_entities_for(tweet.tweet_id)
            symbols = {
                entity.entity_value
                for entity in entities
                if entity.entity_type == "symbol"
            }
            mapped_symbols = {
                mapped
                for symbol in symbols
                if (mapped := map_symbol_to_universe(symbol, universe)) is not None
            }
            if not mapped_symbols:
                mapped_symbols = {"_all"}
            is_kol = tweet.handle in kol_handles or (
                account_map.get(tweet.handle) is not None
                and account_map[tweet.handle].entity_type == "kol"
            )
            day = tweet.posted_at.date()
            for symbol in mapped_symbols:
                key = (day, symbol)
                bucket = buckets.setdefault(key, _SignalBucket())
                bucket.mention_count += 1
                if is_kol:
                    bucket.kol_mention_count += 1
                bucket.bullish_keyword_count += bullish
                bucket.bearish_keyword_count += bearish
                bucket.unique_accounts.add(tweet.handle)

        signals = [
            TweetSignalDaily(
                date=day,
                symbol=symbol,
                mention_count=values.mention_count,
                kol_mention_count=values.kol_mention_count,
                bullish_keyword_count=values.bullish_keyword_count,
                bearish_keyword_count=values.bearish_keyword_count,
                unique_accounts=len(values.unique_accounts),
            )
            for (day, symbol), values in buckets.items()
        ]
        return self.store.upsert_tweet_signals_daily(signals)

    def discover_accounts_from_tweets(self, tweets: list[Tweet]) -> int:
        known = {account.handle for account in self.store.all_twitter_accounts()}
        discovered: list[TwitterAccount] = []
        for tweet in tweets:
            if tweet.handle in known:
                continue
            known.add(tweet.handle)
            discovered.append(
                TwitterAccount(
                    handle=tweet.handle,
                    user_id=None,
                    display_name=tweet.handle,
                    entity_type="trader",
                    chains="",
                    symbols_watch="",
                    confidence=0.3,
                    source="import_discovered",
                    active=False,
                    metadata_json='{"note":"auto-discovered from import"}',
                )
            )
        return self.store.upsert_twitter_accounts(discovered)

    def detect_and_store_alerts(self) -> int:
        from ccquant.twitter.alerts import detect_tweet_alerts

        cfg = self.config.twitter_tracking.signals
        tweets = self.store.all_tweets()
        signals = self.store.all_tweet_signals_daily()
        alerts = detect_tweet_alerts(
            tweets,
            signals,
            window_days=cfg.spike_window_days,
            z_threshold=cfg.spike_z_threshold,
        )
        count = self.store.upsert_tweet_alerts(alerts)
        return count

    def promote_account(self, handle: str) -> bool:
        return self.store.promote_twitter_account(handle)

    def add_account(
        self,
        handle: str,
        *,
        entity_type: str = "trader",
        display_name: str = "",
    ) -> int:
        from ccquant.twitter.accounts import normalize_handle

        account = TwitterAccount(
            handle=normalize_handle(handle),
            user_id=None,
            display_name=display_name or handle,
            entity_type=entity_type.lower(),
            chains="",
            symbols_watch="",
            confidence=0.5,
            source="manual",
            active=True,
        )
        return self.store.upsert_twitter_accounts([account])

    def _persist_import(self, tweets: list[Tweet], *, source: str) -> int:
        if not tweets:
            return 0
        on_conflict = self.config.twitter_tracking.import_config.on_conflict
        inserted = self.store.upsert_tweets(tweets, on_conflict=on_conflict)
        self.discover_accounts_from_tweets(tweets)
        self._update_sync_states(tweets)
        return inserted

    def _update_sync_states(self, tweets: list[Tweet]) -> None:
        now = datetime.now(tz=UTC)
        by_handle: dict[str, list[Tweet]] = {}
        for tweet in tweets:
            by_handle.setdefault(tweet.handle, []).append(tweet)
        for handle, handle_tweets in by_handle.items():
            handle_tweets.sort(key=lambda t: t.posted_at)
            earliest = handle_tweets[0].posted_at
            latest = handle_tweets[-1].posted_at
            latest_id = max(handle_tweets, key=lambda t: t.tweet_id).tweet_id
            state = self.store.get_tweet_sync_state(handle) or TweetSyncState(
                handle=handle
            )
            if state.earliest_at is None or earliest < state.earliest_at:
                state.earliest_at = earliest
            if state.latest_at is None or latest > state.latest_at:
                state.latest_at = latest
                state.latest_tweet_id = latest_id
            state.last_import_at = now
            state.backfill_complete = True
            self.store.upsert_tweet_sync_state(state)
