from __future__ import annotations

from datetime import UTC, datetime

from ccquant.models import Tweet, TweetEntity, TwitterAccount
from ccquant.storage import MarketStore


def test_twitter_storage_roundtrip(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        now = datetime.now(tz=UTC)
        store.upsert_twitter_accounts(
            [
                TwitterAccount(
                    handle="ansem",
                    user_id=None,
                    display_name="Ansem",
                    entity_type="kol",
                    chains="solana",
                    symbols_watch="SOL",
                    confidence=0.9,
                    source="seed",
                    active=True,
                )
            ]
        )
        tweet = Tweet(
            tweet_id="100",
            handle="ansem",
            posted_at=now,
            text="Long $SOL",
            lang="en",
            is_retweet=False,
            is_reply=False,
            reply_to_tweet_id=None,
            conversation_id=None,
            like_count=10,
            retweet_count=2,
            reply_count=1,
            import_source="csv",
            imported_at=now,
            raw_json="{}",
        )
        assert store.upsert_tweets([tweet]) == 1
        store.upsert_tweet_entities(
            [TweetEntity(tweet_id="100", entity_type="symbol", entity_value="SOL")]
        )
        entities = store.tweet_entities_for("100")
        assert len(entities) == 1
        counts = store.twitter_row_counts()
        assert counts["twitter_accounts"] == 1
        assert counts["tweets"] == 1
        assert counts["tweet_entities"] == 1
    finally:
        store.close()
