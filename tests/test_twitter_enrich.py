from __future__ import annotations

from datetime import UTC, date, datetime

from ccquant.models import Tweet, TweetSignalDaily
from ccquant.twitter.alerts import detect_tweet_alerts


def test_detect_symbol_spike_alert() -> None:
    signals = [
        TweetSignalDaily(
            date=date(2025, 6, 1), symbol="SOL", mention_count=2,
            kol_mention_count=0, bullish_keyword_count=0,
            bearish_keyword_count=0, unique_accounts=1,
        ),
        TweetSignalDaily(
            date=date(2025, 6, 2), symbol="SOL", mention_count=1,
            kol_mention_count=0, bullish_keyword_count=0,
            bearish_keyword_count=0, unique_accounts=1,
        ),
        TweetSignalDaily(
            date=date(2025, 6, 3), symbol="SOL", mention_count=3,
            kol_mention_count=0, bullish_keyword_count=0,
            bearish_keyword_count=0, unique_accounts=1,
        ),
        TweetSignalDaily(
            date=date(2025, 6, 4), symbol="SOL", mention_count=20,
            kol_mention_count=5, bullish_keyword_count=3,
            bearish_keyword_count=0, unique_accounts=8,
        ),
    ]
    alerts = detect_tweet_alerts([], signals, window_days=30, z_threshold=2.0)
    spike_alerts = [a for a in alerts if a.alert_type == "symbol_spike"]
    assert spike_alerts
    assert spike_alerts[0].symbols == "SOL"


def test_detect_new_token_call() -> None:
    now = datetime.now(tz=UTC)
    tweets = [
        Tweet(
            tweet_id="1",
            handle="ansem",
            posted_at=now,
            text="Check out $NEWCOIN",
            lang=None,
            is_retweet=False,
            is_reply=False,
            reply_to_tweet_id=None,
            conversation_id=None,
            like_count=0,
            retweet_count=0,
            reply_count=0,
            import_source="csv",
            imported_at=now,
            raw_json="{}",
        )
    ]
    alerts = detect_tweet_alerts(tweets, [], window_days=30, z_threshold=2.0)
    new_calls = [a for a in alerts if a.alert_type == "new_token_call"]
    assert new_calls
    assert new_calls[0].symbols == "NEWCOIN"
