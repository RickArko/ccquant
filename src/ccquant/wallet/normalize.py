from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ccquant.models import WalletTransfer

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def watched_address(transfer: WalletTransfer) -> str:
    if transfer.direction == "inflow":
        return transfer.to_address or transfer.from_address
    if transfer.direction == "outflow":
        return transfer.from_address or transfer.to_address
    return transfer.from_address or transfer.to_address


def transfers_from_solana_tx(
    tx: dict[str, Any],
    *,
    watched: set[str],
    source: str,
) -> list[WalletTransfer]:
    """Normalize a parsed Solana transaction into transfer rows."""
    signature = str(tx.get("signature") or tx.get("tx_hash") or "")
    block_time_raw = tx.get("block_time") or tx.get("blockTime")
    block_time = _parse_block_time(block_time_raw)
    transfers: list[WalletTransfer] = []
    index = 0

    meta = tx.get("meta") or {}
    if isinstance(meta, dict):
        pre_balances = meta.get("preBalances") or meta.get("pre_balances") or []
        post_balances = meta.get("postBalances") or meta.get("post_balances") or []
        account_keys = _account_keys(tx)
        for idx, key in enumerate(account_keys):
            if key not in watched:
                continue
            pre = float(pre_balances[idx]) if idx < len(pre_balances) else 0.0
            post = float(post_balances[idx]) if idx < len(post_balances) else 0.0
            delta_lamports = post - pre
            if delta_lamports == 0:
                continue
            direction = "inflow" if delta_lamports > 0 else "outflow"
            transfers.append(
                WalletTransfer(
                    chain="solana",
                    tx_hash=signature,
                    transfer_index=index,
                    block_time=block_time,
                    from_address=key if direction == "outflow" else "",
                    to_address=key if direction == "inflow" else "",
                    asset_mint_or_contract=SOL_MINT,
                    asset_symbol="SOL",
                    amount=abs(delta_lamports) / 1_000_000_000,
                    amount_usd=None,
                    direction=direction,
                    program_or_method="system",
                    source=source,
                )
            )
            index += 1

        pre_token = meta.get("preTokenBalances") or meta.get("pre_token_balances")
        post_token = meta.get("postTokenBalances") or meta.get("post_token_balances")
        token_deltas = _token_balance_deltas(pre_token, post_token, account_keys)
        for key, mint, delta, symbol in token_deltas:
            if key not in watched:
                continue
            direction = "inflow" if delta > 0 else "outflow"
            transfers.append(
                WalletTransfer(
                    chain="solana",
                    tx_hash=signature,
                    transfer_index=index,
                    block_time=block_time,
                    from_address=key if direction == "outflow" else "",
                    to_address=key if direction == "inflow" else "",
                    asset_mint_or_contract=mint,
                    asset_symbol=symbol,
                    amount=abs(delta),
                    amount_usd=None,
                    direction=direction,
                    program_or_method="spl-token",
                    source=source,
                )
            )
            index += 1

    if not transfers and signature:
        for key in _account_keys(tx):
            if key in watched:
                transfers.append(
                    WalletTransfer(
                        chain="solana",
                        tx_hash=signature,
                        transfer_index=0,
                        block_time=block_time,
                        from_address=key,
                        to_address="",
                        asset_mint_or_contract=SOL_MINT,
                        asset_symbol="SOL",
                        amount=0.0,
                        amount_usd=None,
                        direction="outflow",
                        program_or_method="unknown",
                        source=source,
                    )
                )
                break
    return transfers


def transfers_from_arbitrum_tx(
    tx: dict[str, Any],
    *,
    watched: set[str],
    source: str,
) -> list[WalletTransfer]:
    signature = str(tx.get("hash") or tx.get("tx_hash") or "")
    block_time = _parse_block_time(
        tx.get("block_time") or tx.get("blockTime") or tx.get("timeStamp")
    )
    from_addr = str(tx.get("from") or tx.get("from_address") or "").lower()
    to_addr = str(tx.get("to") or tx.get("to_address") or "").lower()
    value_wei = _parse_wei(tx.get("value"))
    transfers: list[WalletTransfer] = []
    index = 0

    if from_addr in {w.lower() for w in watched}:
        transfers.append(
            WalletTransfer(
                chain="arbitrum",
                tx_hash=signature,
                transfer_index=index,
                block_time=block_time,
                from_address=from_addr,
                to_address=to_addr,
                asset_mint_or_contract="native",
                asset_symbol="ETH",
                amount=value_wei / 1e18,
                amount_usd=None,
                direction="outflow",
                program_or_method=tx.get("method") or "transfer",
                source=source,
            )
        )
        index += 1
    if to_addr in {w.lower() for w in watched}:
        transfers.append(
            WalletTransfer(
                chain="arbitrum",
                tx_hash=signature,
                transfer_index=index,
                block_time=block_time,
                from_address=from_addr,
                to_address=to_addr,
                asset_mint_or_contract="native",
                asset_symbol="ETH",
                amount=value_wei / 1e18,
                amount_usd=None,
                direction="inflow",
                program_or_method=tx.get("method") or "transfer",
                source=source,
            )
        )
    return transfers


def transfers_from_solarchive_row(
    row: dict[str, Any],
    *,
    watched: set[str],
) -> list[WalletTransfer]:
    """Map a SolArchive/BigQuery-style row to transfers."""
    signature = str(row.get("signature") or row.get("tx_signature") or "")
    block_time = _parse_block_time(
        row.get("block_time") or row.get("block_timestamp")
    )
    account_keys = row.get("account_keys") or row.get("accounts") or []
    if isinstance(account_keys, str):
        account_keys = [account_keys]
    keys = [str(k) for k in account_keys]
    matched = [k for k in keys if k in watched]
    if not matched:
        return []
    fee_payer = matched[0]
    return [
        WalletTransfer(
            chain="solana",
            tx_hash=signature,
            transfer_index=0,
            block_time=block_time,
            from_address=fee_payer,
            to_address="",
            asset_mint_or_contract=SOL_MINT,
            asset_symbol="SOL",
            amount=float(row.get("fee") or 0) / 1_000_000_000,
            amount_usd=None,
            direction="outflow",
            program_or_method="solarchive",
            source="solarchive",
        )
    ]


def _account_keys(tx: dict[str, Any]) -> list[str]:
    message = tx.get("transaction", {})
    if isinstance(message, dict):
        message = message.get("message", message)
    if isinstance(message, dict):
        keys = message.get("accountKeys") or message.get("account_keys") or []
        if keys:
            return [
                str(k.get("pubkey", k)) if isinstance(k, dict) else str(k)
                for k in keys
            ]
    keys = tx.get("account_keys") or []
    return [str(k) for k in keys]


def _token_balance_deltas(
    pre: Any,
    post: Any,
    account_keys: list[str],
) -> list[tuple[str, str, float, str | None]]:
    pre_map = _token_balance_map(pre, account_keys)
    post_map = _token_balance_map(post, account_keys)
    results: list[tuple[str, str, float, str | None]] = []
    for key, (mint, amount, symbol) in post_map.items():
        pre_amount = pre_map.get(key, (mint, 0.0, symbol))[1]
        delta = amount - pre_amount
        if delta != 0:
            results.append((key, mint, delta, symbol))
    for key, (mint, amount, symbol) in pre_map.items():
        if key not in post_map and amount != 0:
            results.append((key, mint, -amount, symbol))
    return results


def _token_balance_map(
    balances: Any,
    account_keys: list[str],
) -> dict[str, tuple[str, float, str | None]]:
    mapped: dict[str, tuple[str, float, str | None]] = {}
    if not isinstance(balances, list):
        return mapped
    for bal in balances:
        if not isinstance(bal, dict):
            continue
        idx = int(bal.get("accountIndex") or bal.get("account_index") or 0)
        owner = bal.get("owner") or (
            account_keys[idx] if idx < len(account_keys) else ""
        )
        mint = str(bal.get("mint") or "")
        ui_amount = bal.get("uiTokenAmount") or bal.get("ui_token_amount") or {}
        amount = 0.0
        symbol = None
        if isinstance(ui_amount, dict):
            amount = float(
                ui_amount.get("uiAmount")
                or ui_amount.get("ui_amount")
                or 0
            )
            symbol = ui_amount.get("symbol")
        if owner:
            mapped[str(owner)] = (mint, amount, symbol)
    return mapped


def _parse_wei(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return 0


def _parse_block_time(value: Any) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    raw = str(value).replace(" UTC", "").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(tz=UTC)
