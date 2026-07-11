from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from ccquant.models import WalletAlert, WalletRegistryEntry, WalletTransfer
from ccquant.wallet.normalize import watched_address


def detect_alerts(
    transfers: list[WalletTransfer],
    registry: dict[tuple[str, str], WalletRegistryEntry],
    *,
    since: datetime | None = None,
) -> list[WalletAlert]:
    cutoff = since or datetime.now(tz=UTC) - timedelta(hours=1)
    now = datetime.now(tz=UTC)
    alerts: list[WalletAlert] = []
    for transfer in transfers:
        if transfer.block_time < cutoff:
            continue
        watched = watched_address(transfer)
        entry = registry.get((watched, transfer.chain))
        if entry is None:
            continue
        action = _action_for_transfer(transfer, entry)
        severity = _severity(entry.entity_type, transfer.direction)
        alerts.append(
            WalletAlert(
                address=watched,
                chain=transfer.chain,
                mint_or_contract=transfer.asset_mint_or_contract,
                action=action,
                severity=severity,
                block_time=transfer.block_time,
                tx_hash=transfer.tx_hash,
                alerted_at=now,
                metadata_json=json.dumps(
                    {"label": entry.label, "entity_type": entry.entity_type}
                ),
            )
        )
    return alerts


def _action_for_transfer(
    transfer: WalletTransfer,
    entry: WalletRegistryEntry,
) -> str:
    side = "buy" if transfer.direction == "inflow" else "sell"
    symbol = transfer.asset_symbol or transfer.asset_mint_or_contract[:8]
    return f"{entry.entity_type}_{side}_{symbol}"


def _severity(entity_type: str, direction: str) -> str:
    high_types = {"kol", "smart_money", "insider_cluster"}
    if entity_type in high_types and direction == "inflow":
        return "high"
    if entity_type == "deployer":
        return "medium"
    if entity_type == "whale":
        return "medium"
    return "low"
