from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ccquant.models import Tweet
from ccquant.twitter.accounts import normalize_handle
from ccquant.twitter.import_csv import _parse_bool, _parse_datetime, _parse_int


def _nested_get(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _flatten_json_object(obj: dict[str, Any]) -> dict[str, str]:
    author = obj.get("author") or obj.get("user") or {}
    if not isinstance(author, dict):
        author = {}
    row: dict[str, str] = {
        "tweet_id": str(obj.get("id") or obj.get("tweet_id") or ""),
        "handle": str(
            obj.get("handle")
            or author.get("username")
            or author.get("screen_name")
            or _nested_get(obj, "user", "screen_name")
            or ""
        ),
        "posted_at": str(obj.get("posted_at") or obj.get("created_at") or ""),
        "text": str(
            obj.get("text") or obj.get("full_text") or obj.get("content") or ""
        ),
        "lang": str(obj.get("lang") or ""),
        "like_count": str(
            obj.get("like_count")
            or obj.get("favorite_count")
            or obj.get("public_metrics", {}).get("like_count", "")
            if isinstance(obj.get("public_metrics"), dict)
            else obj.get("like_count") or obj.get("favorite_count") or ""
        ),
        "retweet_count": str(
            obj.get("retweet_count")
            or (
                obj.get("public_metrics", {}).get("retweet_count", "")
                if isinstance(obj.get("public_metrics"), dict)
                else ""
            )
        ),
        "reply_count": str(
            obj.get("reply_count")
            or (
                obj.get("public_metrics", {}).get("reply_count", "")
                if isinstance(obj.get("public_metrics"), dict)
                else ""
            )
        ),
        "is_retweet": str(obj.get("is_retweet") or ""),
        "is_reply": str(obj.get("is_reply") or ""),
        "reply_to_tweet_id": str(obj.get("reply_to_tweet_id") or ""),
        "conversation_id": str(obj.get("conversation_id") or ""),
    }
    return row


def _tweet_from_object(
    obj: dict[str, Any],
    *,
    import_source: str,
    imported_at: datetime,
) -> Tweet:
    row = _flatten_json_object(obj)
    return Tweet(
        tweet_id=row["tweet_id"].strip(),
        handle=normalize_handle(row["handle"]),
        posted_at=_parse_datetime(row["posted_at"]),
        text=row["text"].strip(),
        lang=row["lang"].strip() or None,
        is_retweet=_parse_bool(row["is_retweet"]),
        is_reply=_parse_bool(row["is_reply"]),
        reply_to_tweet_id=row["reply_to_tweet_id"].strip() or None,
        conversation_id=row["conversation_id"].strip() or None,
        like_count=_parse_int(row["like_count"]),
        retweet_count=_parse_int(row["retweet_count"]),
        reply_count=_parse_int(row["reply_count"]),
        import_source=import_source,
        imported_at=imported_at,
        raw_json=json.dumps(obj, ensure_ascii=False),
    )


def load_tweets_jsonl(path: Path, *, import_source: str = "jsonl") -> list[Tweet]:
    imported_at = datetime.now(tz=UTC)
    tweets: list[Tweet] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            if not (obj.get("posted_at") or obj.get("created_at")):
                continue
            tweets.append(
                _tweet_from_object(
                    obj, import_source=import_source, imported_at=imported_at
                )
            )
    return tweets
