from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from ccquant.wallet.tail_bitcoin import tail_bitcoin_wallets


@pytest.mark.asyncio
async def test_tail_bitcoin_wallets_parses_mempool_response() -> None:
    payload = [
        {
            "txid": "tx1",
            "status": {"block_time": 1_700_000_000},
            "vin": [
                {
                    "is_coinbase": False,
                    "prevout": {
                        "scriptpubkey_address": "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
                        "value": 100_000_000,
                        "scriptpubkey_type": "p2pkh",
                    },
                }
            ],
            "vout": [
                {
                    "scriptpubkey_address": "34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi",
                    "value": 90_000_000,
                    "scriptpubkey_type": "p2pkh",
                }
            ],
        }
    ]
    mock_request = httpx.Request("GET", "https://mempool.space/api/address/x/txs")
    mock_response = httpx.Response(200, json=payload, request=mock_request)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(return_value=mock_response)

    transfers = await tail_bitcoin_wallets(
        client,
        api_url="https://mempool.space/api",
        addresses=["1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"],
        delay_seconds=0.0,
    )
    assert len(transfers) == 1
    assert transfers[0].chain == "bitcoin"
    assert transfers[0].direction == "outflow"
    client.get.assert_called_once()
