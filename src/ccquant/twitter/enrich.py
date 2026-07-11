from __future__ import annotations

import re

from ccquant.config import TwitterEnrichmentConfig
from ccquant.models import Tweet, TweetEntity

_CASHTAG_RE = re.compile(r"\$([A-Z]{2,10})\b")
_SOL_DOMAIN_RE = re.compile(r"\b([\w-]{2,32}\.sol)\b", re.IGNORECASE)
_ETH_ADDRESS_RE = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")
_BTC_BECH32_RE = re.compile(r"\b(bc1[a-z0-9]{25,62})\b", re.IGNORECASE)
_BTC_LEGACY_RE = re.compile(r"\b([13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_SOL_ADDRESS_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def extract_entities(
    tweet: Tweet,
    cfg: TwitterEnrichmentConfig,
) -> list[TweetEntity]:
    entities: list[TweetEntity] = []
    seen: set[tuple[str, str]] = set()
    text = tweet.text

    if cfg.extract_cashtags:
        for match in _CASHTAG_RE.finditer(text):
            tag = match.group(1).upper()
            _add_entity(entities, seen, tweet.tweet_id, "cashtag", tag)
            _add_entity(entities, seen, tweet.tweet_id, "symbol", tag)

    if cfg.extract_sol_domains:
        for match in _SOL_DOMAIN_RE.finditer(text):
            domain = match.group(1).lower()
            _add_entity(entities, seen, tweet.tweet_id, "sol_domain", domain)

    if cfg.extract_addresses:
        for match in _ETH_ADDRESS_RE.finditer(text):
            _add_entity(
                entities, seen, tweet.tweet_id, "eth_address", match.group(1).lower()
            )
        for match in _BTC_BECH32_RE.finditer(text):
            _add_entity(
                entities,
                seen,
                tweet.tweet_id,
                "btc_address",
                match.group(1).lower(),
            )
        for match in _BTC_LEGACY_RE.finditer(text):
            candidate = match.group(1)
            _add_entity(
                entities,
                seen,
                tweet.tweet_id,
                "btc_address",
                candidate,
            )
        for match in _SOL_ADDRESS_RE.finditer(text):
            candidate = match.group(1)
            if candidate.startswith("0x"):
                continue
            if _BTC_LEGACY_RE.fullmatch(candidate):
                continue
            if len(candidate) < 32:
                continue
            _add_entity(
                entities, seen, tweet.tweet_id, "sol_address", candidate
            )

    for match in _URL_RE.finditer(text):
        _add_entity(entities, seen, tweet.tweet_id, "url", match.group(0))

    return entities


def score_sentiment(
    text: str,
    cfg: TwitterEnrichmentConfig,
) -> tuple[int, int]:
    if not cfg.keyword_sentiment:
        return 0, 0
    lowered = text.lower()
    bullish = sum(1 for kw in cfg.bullish_keywords if kw in lowered)
    bearish = sum(1 for kw in cfg.bearish_keywords if kw in lowered)
    return bullish, bearish


def map_symbol_to_universe(
    symbol: str,
    universe_symbols: set[str],
) -> str | None:
    upper = symbol.upper()
    if upper in universe_symbols:
        return upper
    return None


def _add_entity(
    entities: list[TweetEntity],
    seen: set[tuple[str, str]],
    tweet_id: str,
    entity_type: str,
    value: str,
) -> None:
    key = (entity_type, value)
    if key in seen:
        return
    seen.add(key)
    entities.append(
        TweetEntity(tweet_id=tweet_id, entity_type=entity_type, entity_value=value)
    )
