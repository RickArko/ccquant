from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ccquant.models import WalletTransfer
from ccquant.wallet.normalize import transfers_from_arbitrum_tx

CAMP_BASE_URL = "https://engine.camp/v1"


async def tail_arbitrum_wallets(
    client: httpx.AsyncClient,
    *,
    addresses: list[str],
    delay_seconds: float = 1.0,
) -> list[WalletTransfer]:
    transfers: list[WalletTransfer] = []
    watched = set(addresses)
    for address in addresses:
        txs = await _fetch_wallet_txs(client, address=address)
        for tx in txs:
            transfers.extend(
                transfers_from_arbitrum_tx(
                    tx,
                    watched=watched,
                    source="camp_tail",
                )
            )
        await asyncio.sleep(delay_seconds)
    return transfers


async def _fetch_wallet_txs(
    client: httpx.AsyncClient,
    *,
    address: str,
) -> list[dict[str, Any]]:
    url = f"{CAMP_BASE_URL}/wallet/{address}/txs"
    try:
        resp = await client.get(url, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            txs = data.get("transactions") or data.get("txs") or []
            if isinstance(txs, list):
                return [item for item in txs if isinstance(item, dict)]
    except httpx.HTTPError:
        return []
    return []
