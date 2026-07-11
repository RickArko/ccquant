from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx

from ccquant.models import WalletTransfer
from ccquant.wallet.normalize import transfers_from_solana_tx


async def tail_solana_wallets(
    client: httpx.AsyncClient,
    *,
    rpc_url: str,
    addresses: list[str],
    before: str | None = None,
    limit: int = 20,
    delay_seconds: float = 1.0,
) -> list[WalletTransfer]:
    transfers: list[WalletTransfer] = []
    watched = set(addresses)
    for address in addresses:
        sigs = await _fetch_signatures(
            client,
            rpc_url=rpc_url,
            address=address,
            before=before,
            limit=limit,
        )
        for sig in sigs:
            tx = await _fetch_transaction(client, rpc_url=rpc_url, signature=sig)
            if tx:
                transfers.extend(
                    transfers_from_solana_tx(
                        tx,
                        watched=watched,
                        source="rpc_tail",
                    )
                )
            await asyncio.sleep(delay_seconds)
    return transfers


async def _fetch_signatures(
    client: httpx.AsyncClient,
    *,
    rpc_url: str,
    address: str,
    before: str | None,
    limit: int,
) -> list[str]:
    params: dict[str, Any] = {"limit": limit}
    if before:
        params["before"] = before
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, params],
    }
    try:
        resp = await client.post(rpc_url, json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return []
    result = data.get("result") or []
    if not isinstance(result, list):
        return []
    return [str(item.get("signature")) for item in result if item.get("signature")]


async def _fetch_transaction(
    client: httpx.AsyncClient,
    *,
    rpc_url: str,
    signature: str,
) -> dict[str, Any] | None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "json", "maxSupportedTransactionVersion": 0},
        ],
    }
    try:
        resp = await client.post(rpc_url, json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return None
    result = data.get("result")
    if isinstance(result, dict):
        result["signature"] = signature
        return result
    return None


def latest_block_time(transfers: list[WalletTransfer]) -> datetime | None:
    if not transfers:
        return None
    return max(t.block_time for t in transfers)
