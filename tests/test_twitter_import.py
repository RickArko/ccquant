from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ccquant.config import TwitterEnrichmentConfig, load_config
from ccquant.models import Tweet
from ccquant.storage import MarketStore
from ccquant.twitter.accounts import load_seed_accounts, normalize_handle
from ccquant.twitter.enrich import (
    extract_entities,
    map_symbol_to_universe,
    score_sentiment,
)
from ccquant.twitter.import_csv import load_tweets_csv
from ccquant.twitter.import_jsonl import load_tweets_jsonl
from ccquant.twitter.sync import TwitterSync

FIXTURE_CSV = Path("tests/fixtures/twitter/sample_tweets.csv")
FIXTURE_JSONL = Path("tests/fixtures/twitter/sample_tweets.jsonl")
SEED_CSV = Path("config/seeds/twitter_accounts_seed.csv")


def test_normalize_handle() -> None:
    assert normalize_handle("@Ansem") == "ansem"
    assert normalize_handle("lookonchain") == "lookonchain"


def test_committed_seed_accounts_load() -> None:
    accounts = load_seed_accounts(SEED_CSV)
    assert len(accounts) >= 30
    types = {account.entity_type for account in accounts}
    assert "kol" in types
    assert "alert_bot" in types


def test_load_fixture_csv() -> None:
    tweets = load_tweets_csv(FIXTURE_CSV)
    assert len(tweets) >= 100
    handles = {tweet.handle for tweet in tweets}
    assert len(handles) == 3


def test_load_fixture_jsonl(tmp_path: Path) -> None:
    tweets = load_tweets_csv(FIXTURE_CSV)
    jsonl = tmp_path / "sample.jsonl"
    import json

    with jsonl.open("w", encoding="utf-8") as handle:
        for tweet in tweets[:5]:
            handle.write(
                json.dumps(
                    {
                        "id": tweet.tweet_id,
                        "created_at": tweet.posted_at.isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "author": {"username": tweet.handle},
                        "text": tweet.text,
                    }
                )
                + "\n"
            )
    loaded = load_tweets_jsonl(jsonl)
    assert len(loaded) == 5


def test_extract_entities_cashtags_and_addresses() -> None:
    cfg = TwitterEnrichmentConfig()
    tweet = Tweet(
        tweet_id="1",
        handle="ansem",
        posted_at=datetime.now(tz=UTC),
        text=(
            "Long $SOL and wallet FoKTT3dKz8Kz8Kz8Kz8Kz8Kz8Kz8Kz8Kz8Kz8Kz8Kz8Kz8 "
            "mynode.sol"
        ),
        lang=None,
        is_retweet=False,
        is_reply=False,
        reply_to_tweet_id=None,
        conversation_id=None,
        like_count=0,
        retweet_count=0,
        reply_count=0,
        import_source="csv",
        imported_at=datetime.now(tz=UTC),
        raw_json="{}",
    )
    entities = extract_entities(tweet, cfg)
    types = {entity.entity_type for entity in entities}
    assert "symbol" in types
    assert "sol_domain" in types


def test_extract_entities_btc_addresses() -> None:
    cfg = TwitterEnrichmentConfig()
    tweet = Tweet(
        tweet_id="2",
        handle="saylor",
        posted_at=datetime.now(tz=UTC),
        text=(
            "Treasury wallet 1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj "
            "and bc1qjasf9z3h7l3jkaware86a4s4ut9t928cerovd"
        ),
        lang=None,
        is_retweet=False,
        is_reply=False,
        reply_to_tweet_id=None,
        conversation_id=None,
        like_count=0,
        retweet_count=0,
        reply_count=0,
        import_source="csv",
        imported_at=datetime.now(tz=UTC),
        raw_json="{}",
    )
    entities = extract_entities(tweet, cfg)
    btc_addrs = {
        entity.entity_value
        for entity in entities
        if entity.entity_type == "btc_address"
    }
    assert "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj" in btc_addrs
    assert "bc1qjasf9z3h7l3jkaware86a4s4ut9t928cerovd" in btc_addrs


def test_score_sentiment() -> None:
    cfg = TwitterEnrichmentConfig()
    bullish, bearish = score_sentiment("Long and buy the dip", cfg)
    assert bullish >= 2
    _, bear = score_sentiment("Going to dump and rug", cfg)
    assert bear >= 2


def test_map_symbol_to_universe() -> None:
    universe = {"BTC", "SOL", "ETH"}
    assert map_symbol_to_universe("sol", universe) == "SOL"
    assert map_symbol_to_universe("WIF", universe) is None


def test_upsert_tweets_idempotent(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        tweets = load_tweets_csv(FIXTURE_CSV)[:10]
        assert store.upsert_tweets(tweets, on_conflict="skip") == 10
        assert store.upsert_tweets(tweets, on_conflict="skip") == 0
        assert len(store.all_tweets()) == 10
    finally:
        store.close()


def test_import_and_enrich_pipeline(tmp_path: Path) -> None:
    db = tmp_path / "ccquant.duckdb"
    inbox = tmp_path / "inbox"
    archive = tmp_path / "archive"
    inbox.mkdir()
    fixture_copy = inbox / "sample_tweets.csv"
    fixture_copy.write_text(FIXTURE_CSV.read_text(encoding="utf-8"), encoding="utf-8")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
database: {db}
twitter_tracking:
  enabled: true
  accounts_seed: config/seeds/twitter_accounts_seed.csv
  import:
    inbox_dir: {inbox}
    archive_dir: {archive}
    formats: [csv, jsonl]
    on_conflict: skip
""",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    store = MarketStore(db)
    syncer = TwitterSync(store, cfg)
    try:
        syncer.load_accounts()
        results = syncer.sync_all()
        assert results["imported"] == 120
        assert results["files"] == 1
        assert (archive / "sample_tweets.csv").exists()
        assert store.twitter_row_counts()["tweets"] == 120
        assert store.twitter_row_counts()["tweet_entities"] > 0
        assert store.twitter_row_counts()["tweet_signals_daily"] > 0
        # idempotent re-run
        results2 = syncer.sync_all()
        assert results2.get("imported", 0) == 0
        assert store.twitter_row_counts()["tweets"] == 120
    finally:
        store.close()


def test_discovered_account_review(tmp_path: Path) -> None:
    db = tmp_path / "ccquant.duckdb"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"database: {db}\n", encoding="utf-8")
    cfg = load_config(config_path)
    store = MarketStore(db)
    syncer = TwitterSync(store, cfg)
    try:
        tweet = Tweet(
            tweet_id="999",
            handle="newtrader123",
            posted_at=datetime.now(tz=UTC),
            text="hello $BTC",
            lang=None,
            is_retweet=False,
            is_reply=False,
            reply_to_tweet_id=None,
            conversation_id=None,
            like_count=0,
            retweet_count=0,
            reply_count=0,
            import_source="csv",
            imported_at=datetime.now(tz=UTC),
            raw_json="{}",
        )
        syncer.discover_accounts_from_tweets([tweet])
        discovered = store.discovered_twitter_accounts()
        assert any(account.handle == "newtrader123" for account in discovered)
        assert syncer.promote_account("newtrader123")
        active = store.active_twitter_accounts()
        assert any(account.handle == "newtrader123" for account in active)
    finally:
        store.close()
