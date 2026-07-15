from __future__ import annotations

import asyncio

import httpx

from ccquant.models import WalletTransfer
from ccquant.wallet.normalize import transfers_from_bitcoin_tx


async def tail_bitcoin_wallets(
    client: httpx.AsyncClient,
    *,
    api_url: str,
    addresses: list[str],
    delay_seconds: float = 1.0,
) -> list[WalletTransfer]:
    transfers: list[WalletTransfer] = []
    base = api_url.rstrip("/")
    watched = set(addresses)
    for address in addresses:
        url = f"{base}/address/{address}/txs"
        try:
            resp = await client.get(url, timeout=30.0)
            resp.raise_for_status()
            txs = resp.json()
        except (httpx.HTTPError, ValueError):
            await asyncio.sleep(delay_seconds)
            continue
        if not isinstance(txs, list):
            await asyncio.sleep(delay_seconds)
            continue
        for tx in txs[:25]:
            if isinstance(tx, dict):
                transfers.extend(
                    transfers_from_bitcoin_tx(
                        tx,
                        watched=watched,
                        source="mempool_tail",
                    )
                )
        await asyncio.sleep(delay_seconds)
    return transfers
