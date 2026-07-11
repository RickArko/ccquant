from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from ccquant.models import Tweet, TweetAlert, TweetSignalDaily


def detect_tweet_alerts(
    tweets: list[Tweet],
    signals: list[TweetSignalDaily],
    *,
    window_days: int = 30,
    z_threshold: float = 2.0,
) -> list[TweetAlert]:
    now = datetime.now(tz=UTC)
    alerts: list[TweetAlert] = []
    alerts.extend(_detect_symbol_spikes(signals, window_days, z_threshold, now))
    alerts.extend(_detect_kol_cluster_mentions(tweets, now))
    alerts.extend(_detect_new_token_calls(tweets, now))
    return alerts


def _detect_symbol_spikes(
    signals: list[TweetSignalDaily],
    window_days: int,
    z_threshold: float,
    now: datetime,
) -> list[TweetAlert]:
    by_symbol: dict[str, list[TweetSignalDaily]] = defaultdict(list)
    for signal in signals:
        if signal.symbol == "_all":
            continue
        by_symbol[signal.symbol].append(signal)

    alerts: list[TweetAlert] = []
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda row: row.date)
        if len(rows) < 3:
            continue
        recent = rows[-window_days:]
        counts = [row.mention_count for row in recent[:-1]]
        if len(counts) < 2:
            continue
        latest = recent[-1]
        mean = statistics.mean(counts)
        stdev = statistics.pstdev(counts)
        if stdev == 0:
            continue
        z = (latest.mention_count - mean) / stdev
        if z >= z_threshold:
            alerts.append(
                TweetAlert(
                    tweet_id=f"spike-{symbol}-{latest.date.isoformat()}",
                    handle="_system",
                    alert_type="symbol_spike",
                    severity="high" if z >= z_threshold + 1 else "medium",
                    symbols=symbol,
                    posted_at=datetime.combine(
                        latest.date, datetime.min.time(), tzinfo=UTC
                    ),
                    alerted_at=now,
                    metadata_json=json.dumps(
                        {
                            "z_score": round(z, 2),
                            "mention_count": latest.mention_count,
                            "baseline_mean": round(mean, 2),
                        }
                    ),
                )
            )
    return alerts


def _detect_kol_cluster_mentions(
    tweets: list[Tweet],
    now: datetime,
) -> list[TweetAlert]:
    import re

    cashtag_re = re.compile(r"\$([A-Z]{2,10})\b")
    window = timedelta(hours=1)
    recent = [tweet for tweet in tweets if tweet.posted_at >= now - timedelta(days=7)]
    by_symbol_window: dict[str, list[Tweet]] = defaultdict(list)

    for tweet in recent:
        for match in cashtag_re.finditer(tweet.text):
            symbol = match.group(1)
            by_symbol_window[symbol].append(tweet)

    alerts: list[TweetAlert] = []
    for symbol, symbol_tweets in by_symbol_window.items():
        symbol_tweets.sort(key=lambda t: t.posted_at)
        for idx, anchor in enumerate(symbol_tweets):
            cluster_handles = {anchor.handle}
            for other in symbol_tweets[idx + 1 :]:
                if other.posted_at - anchor.posted_at > window:
                    break
                cluster_handles.add(other.handle)
            if len(cluster_handles) >= 3:
                alerts.append(
                    TweetAlert(
                        tweet_id=anchor.tweet_id,
                        handle=anchor.handle,
                        alert_type="kol_cluster_mention",
                        severity="medium",
                        symbols=symbol,
                        posted_at=anchor.posted_at,
                        alerted_at=now,
                        metadata_json=json.dumps(
                            {"accounts": sorted(cluster_handles), "symbol": symbol}
                        ),
                    )
                )
                break
    return alerts


def _detect_new_token_calls(
    tweets: list[Tweet],
    now: datetime,
) -> list[TweetAlert]:
    import re

    cashtag_re = re.compile(r"\$([A-Z]{2,10})\b")
    seen_symbols: set[str] = set()
    first_mentions: dict[str, Tweet] = {}
    ordered = sorted(tweets, key=lambda t: t.posted_at)
    for tweet in ordered:
        for match in cashtag_re.finditer(tweet.text):
            symbol = match.group(1)
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            first_mentions[symbol] = tweet

    alerts: list[TweetAlert] = []
    cutoff = now - timedelta(days=7)
    for symbol, tweet in first_mentions.items():
        if tweet.posted_at < cutoff:
            continue
        alerts.append(
            TweetAlert(
                tweet_id=tweet.tweet_id,
                handle=tweet.handle,
                alert_type="new_token_call",
                severity="low",
                symbols=symbol,
                posted_at=tweet.posted_at,
                alerted_at=now,
                metadata_json=json.dumps({"first_mention": True}),
            )
        )
    return alerts
