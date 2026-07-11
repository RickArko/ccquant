from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from ccquant.models import (
    WalletIdentity,
    WalletIdentityLink,
    WalletRegistryEntry,
)


def load_seed_registry(path: Path) -> list[WalletRegistryEntry]:
    if not path.exists():
        raise FileNotFoundError(f"wallet seed file not found: {path}")
    now = datetime.now(tz=UTC)
    entries: list[WalletRegistryEntry] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entries.append(
                WalletRegistryEntry(
                    address=row["address"].strip(),
                    chain=row["chain"].strip().lower(),
                    label=row["label"].strip(),
                    entity_type=row["entity_type"].strip().lower(),
                    confidence=float(row.get("confidence") or 0.5),
                    source=row.get("source", "manual").strip().lower(),
                    discovered_at=now,
                    active=True,
                    metadata_json=row.get("metadata_json", "{}").strip() or "{}",
                )
            )
    return entries


def load_seed_identities(path: Path) -> list[WalletIdentity]:
    if not path.exists():
        return []
    identities: list[WalletIdentity] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            identities.append(
                WalletIdentity(
                    identity_id=row["identity_id"].strip(),
                    display_name=row["display_name"].strip(),
                    category=row["category"].strip().lower(),
                    description=row.get("description", "").strip(),
                    source_url=row.get("source_url", "").strip(),
                    active=str(row.get("active", "true")).strip().lower()
                    != "false",
                )
            )
    return identities


def load_seed_identity_links(path: Path) -> list[WalletIdentityLink]:
    if not path.exists():
        return []
    now = datetime.now(tz=UTC)
    links: list[WalletIdentityLink] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            links.append(
                WalletIdentityLink(
                    address=row["address"].strip(),
                    chain=row["chain"].strip().lower(),
                    identity_id=row["identity_id"].strip(),
                    link_type=row.get("link_type", "associated").strip().lower(),
                    confidence=float(row.get("confidence") or 0.5),
                    source=row.get("source", "manual").strip().lower(),
                    linked_at=now,
                )
            )
    return links


def resolve_seed_path(configured: Path) -> Path:
    if configured.exists():
        return configured
    fallback = Path("data/seeds/wallet_registry_seed.csv")
    if fallback.exists():
        return fallback
    return configured


def resolve_identity_seed_path(configured: Path) -> Path:
    if configured.exists():
        return configured
    fallback = Path("data/seeds/wallet_identities_seed.csv")
    if fallback.exists():
        return fallback
    return configured


def resolve_identity_links_seed_path(configured: Path) -> Path:
    if configured.exists():
        return configured
    fallback = Path("data/seeds/wallet_identity_links_seed.csv")
    if fallback.exists():
        return fallback
    return configured
