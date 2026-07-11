from __future__ import annotations

import csv
import re
from pathlib import Path

from ccquant.models import TwitterAccount

_HANDLE_RE = re.compile(r"^@?(?P<handle>[a-zA-Z0-9_]{1,15})$")


def normalize_handle(raw: str) -> str:
    value = raw.strip().lower()
    if value.startswith("@"):
        value = value[1:]
    match = _HANDLE_RE.match(value)
    if match is None:
        raise ValueError(f"invalid twitter handle: {raw!r}")
    return match.group("handle")


def load_seed_accounts(path: Path) -> list[TwitterAccount]:
    if not path.exists():
        raise FileNotFoundError(f"twitter accounts seed not found: {path}")
    accounts: list[TwitterAccount] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            accounts.append(
                TwitterAccount(
                    handle=normalize_handle(row["handle"]),
                    user_id=(row.get("user_id") or "").strip() or None,
                    display_name=row.get("display_name", row["handle"]).strip(),
                    entity_type=row.get("entity_type", "kol").strip().lower(),
                    chains=row.get("chains", "").strip(),
                    symbols_watch=row.get("symbols_watch", "").strip(),
                    confidence=float(row.get("confidence") or 0.5),
                    source=row.get("source", "seed").strip().lower(),
                    active=True,
                    metadata_json=row.get("metadata_json", "{}").strip() or "{}",
                )
            )
    return accounts


def resolve_accounts_seed(configured: Path) -> Path:
    if configured.exists():
        return configured
    fallback = Path("data/seeds/twitter_accounts_seed.csv")
    if fallback.exists():
        return fallback
    return configured
