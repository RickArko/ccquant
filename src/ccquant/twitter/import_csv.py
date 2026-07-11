from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from ccquant.models import Tweet
from ccquant.twitter.accounts import normalize_handle

_BOOL_TRUE = {"true", "1", "yes", "y"}


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _BOOL_TRUE


def _parse_int(value: str | None, default: int = 0) -> int:
    if value is None or value.strip() == "":
        return default
    return int(float(value))


def _parse_datetime(value: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _tweet_from_row(
    row: dict[str, str],
    *,
    import_source: str,
    imported_at: datetime,
) -> Tweet:
    tweet_id = (row.get("tweet_id") or row.get("id") or "").strip()
    handle = normalize_handle(row.get("handle") or row.get("username") or "")
    posted_at = _parse_datetime(row["posted_at"])
    text = (row.get("text") or row.get("full_text") or "").strip()
    return Tweet(
        tweet_id=tweet_id,
        handle=handle,
        posted_at=posted_at,
        text=text,
        lang=(row.get("lang") or "").strip() or None,
        is_retweet=_parse_bool(row.get("is_retweet")),
        is_reply=_parse_bool(row.get("is_reply")),
        reply_to_tweet_id=(row.get("reply_to_tweet_id") or "").strip() or None,
        conversation_id=(row.get("conversation_id") or "").strip() or None,
        like_count=_parse_int(row.get("like_count") or row.get("favorite_count")),
        retweet_count=_parse_int(row.get("retweet_count")),
        reply_count=_parse_int(row.get("reply_count")),
        import_source=import_source,
        imported_at=imported_at,
        raw_json=json.dumps(row, ensure_ascii=False),
    )


def load_tweets_csv(path: Path, *, import_source: str = "csv") -> list[Tweet]:
    imported_at = datetime.now(tz=UTC)
    tweets: list[Tweet] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row.get("posted_at"):
                continue
            tweets.append(
                _tweet_from_row(
                    row, import_source=import_source, imported_at=imported_at
                )
            )
    return tweets
