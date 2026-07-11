from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from ccquant.models import WalletRegistryEntry


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


def resolve_seed_path(configured: Path) -> Path:
    if configured.exists():
        return configured
    fallback = Path("data/seeds/wallet_registry_seed.csv")
    if fallback.exists():
        return fallback
    return configured
