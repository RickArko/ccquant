from __future__ import annotations

from datetime import UTC, datetime

from ccquant.wallet.normalize import (
    BTC_ASSET,
    transfer_from_bitcoin_bq_row,
    transfers_from_bitcoin_tx,
)


def test_transfers_from_bitcoin_tx_output_inflow() -> None:
    watched = {"1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"}
    tx = {
        "txid": "abc123",
        "status": {"block_time": 1_700_000_000},
        "vin": [],
        "vout": [
            {
                "n": 0,
                "value": 50_000_000,
                "scriptpubkey_type": "p2pkh",
                "scriptpubkey_address": "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
            }
        ],
    }
    transfers = transfers_from_bitcoin_tx(tx, watched=watched, source="mempool")
    assert len(transfers) == 1
    assert transfers[0].chain == "bitcoin"
    assert transfers[0].direction == "inflow"
    assert transfers[0].to_address == "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"
    assert transfers[0].amount == 0.5
    assert transfers[0].transfer_index == 0
    assert transfers[0].asset_mint_or_contract == BTC_ASSET


def test_transfers_from_bitcoin_tx_uses_leg_index_not_watched_counter() -> None:
    watched = {"1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"}
    tx = {
        "txid": "leg-idx",
        "status": {"block_time": 1_700_000_000},
        "vin": [],
        "vout": [
            {
                "n": 0,
                "value": 10_000_000,
                "scriptpubkey_type": "p2pkh",
                "scriptpubkey_address": "34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi",
            },
            {
                "n": 2,
                "value": 50_000_000,
                "scriptpubkey_type": "p2pkh",
                "scriptpubkey_address": "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
            },
        ],
    }
    transfers = transfers_from_bitcoin_tx(tx, watched=watched, source="mempool")
    assert len(transfers) == 1
    assert transfers[0].transfer_index == 2


def test_transfers_from_bitcoin_tx_input_outflow() -> None:
    watched = {"1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"}
    tx = {
        "hash": "def456",
        "block_timestamp": datetime(2024, 1, 1, tzinfo=UTC),
        "inputs": [
            {
                "index": 0,
                "value": 100_000_000,
                "type": "p2wpkh",
                "addresses": ["1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"],
            }
        ],
        "outputs": [
            {
                "index": 0,
                "value": 99_000_000,
                "type": "p2wpkh",
                "addresses": ["34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi"],
            }
        ],
    }
    transfers = transfers_from_bitcoin_tx(tx, watched=watched, source="bigquery")
    outflows = [t for t in transfers if t.direction == "outflow"]
    assert len(outflows) == 1
    assert outflows[0].from_address == "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"
    assert outflows[0].to_address == "34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi"
    assert outflows[0].amount == 1.0
    assert outflows[0].transfer_index == 0


def test_transfer_from_bitcoin_bq_row() -> None:
    row = {
        "hash": "hash789",
        "block_time": datetime(2024, 6, 1, tzinfo=UTC),
        "leg_index": 2,
        "address": "3QJmV3qfvL9SuYo34YihAf3sEmW6uKcBAS",
        "value_sats": 250_000_000,
        "script_type": "p2sh",
        "direction": "inflow",
        "counterparty": "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
    }
    transfer = transfer_from_bitcoin_bq_row(row, source="bigquery")
    assert transfer is not None
    assert transfer.tx_hash == "hash789"
    assert transfer.transfer_index == 2
    assert transfer.amount == 2.5
    assert transfer.program_or_method == "p2sh"
    assert transfer.from_address == "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"
    assert transfer.to_address == "3QJmV3qfvL9SuYo34YihAf3sEmW6uKcBAS"
