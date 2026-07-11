from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ccquant.models import (
    WalletAlert,
    WalletRegistryEntry,
    WalletTransfer,
)
from ccquant.storage import MarketStore
from ccquant.wallet.alerts import detect_alerts
from ccquant.wallet.discovery import match_holder_amount, score_wallet_performance
from ccquant.wallet.normalize import (
    transfers_from_arbitrum_tx,
    transfers_from_solana_tx,
    transfers_from_solarchive_row,
    watched_address,
)
from ccquant.wallet.seeds import load_seed_registry


def test_load_seed_registry(tmp_path) -> None:
    seed = tmp_path / "seed.csv"
    seed.write_text(
        "address,chain,label,entity_type,confidence,source,metadata_json\n"
        "Addr1,solana,Test Wallet,kol,0.8,manual,{}\n",
        encoding="utf-8",
    )
    entries = load_seed_registry(seed)
    assert len(entries) == 1
    assert entries[0].address == "Addr1"
    assert entries[0].entity_type == "kol"


def test_committed_seed_file_loads() -> None:
    seed = Path("config/seeds/wallet_registry_seed.csv")
    assert seed.exists()
    entries = load_seed_registry(seed)
    assert len(entries) >= 40
    chains = {entry.chain for entry in entries}
    assert "solana" in chains
    assert "arbitrum" in chains


def test_upsert_wallet_registry_is_idempotent(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        now = datetime.now(tz=UTC)
        entries = [
            WalletRegistryEntry(
                address="Addr1",
                chain="solana",
                label="Wallet A",
                entity_type="kol",
                confidence=0.8,
                source="manual",
                discovered_at=now,
                active=True,
            ),
            WalletRegistryEntry(
                address="Addr1",
                chain="solana",
                label="Wallet A Updated",
                entity_type="kol",
                confidence=0.9,
                source="manual",
                discovered_at=now,
                active=True,
            ),
        ]
        assert store.upsert_wallet_registry(entries) == 2
        active = store.active_wallet_registry()
        assert len(active) == 1
        assert active[0].label == "Wallet A Updated"
        assert active[0].confidence == 0.9
    finally:
        store.close()


def test_upsert_wallet_transfers_is_idempotent(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        now = datetime.now(tz=UTC)
        transfer = WalletTransfer(
            chain="solana",
            tx_hash="sig1",
            transfer_index=0,
            block_time=now,
            from_address="Addr1",
            to_address="Addr2",
            asset_mint_or_contract="mint1",
            asset_symbol="SOL",
            amount=1.5,
            amount_usd=None,
            direction="outflow",
            program_or_method="system",
            source="test",
        )
        assert store.upsert_wallet_transfers([transfer]) == 1
        updated = WalletTransfer(
            chain="solana",
            tx_hash="sig1",
            transfer_index=0,
            block_time=now,
            from_address="Addr1",
            to_address="Addr2",
            asset_mint_or_contract="mint1",
            asset_symbol="SOL",
            amount=2.0,
            amount_usd=None,
            direction="outflow",
            program_or_method="system",
            source="test",
        )
        assert store.upsert_wallet_transfers([updated]) == 1
        row = store.connection.execute(
            "select amount from wallet_transfers where tx_hash = 'sig1'"
        ).fetchone()
        assert row is not None and row[0] == 2.0
    finally:
        store.close()


def test_normalize_solana_tx() -> None:
    watched = {"WalletA"}
    tx = {
        "signature": "abc123",
        "block_time": 1_700_000_000,
        "transaction": {
            "message": {
                "accountKeys": ["WalletA", "WalletB"],
            }
        },
        "meta": {
            "preBalances": [1_000_000_000, 0],
            "postBalances": [500_000_000, 500_000_000],
        },
    }
    transfers = transfers_from_solana_tx(tx, watched=watched, source="test")
    assert len(transfers) == 1
    assert transfers[0].direction == "outflow"
    assert transfers[0].amount == pytest.approx(0.5)


def test_normalize_arbitrum_tx() -> None:
    watched = {"0xabc"}
    tx = {
        "hash": "0xhash",
        "block_time": "2026-07-01T12:00:00+00:00",
        "from": "0xabc",
        "to": "0xdef",
        "value": 1e18,
    }
    transfers = transfers_from_arbitrum_tx(tx, watched=watched, source="test")
    assert len(transfers) == 1
    assert transfers[0].asset_symbol == "ETH"


def test_normalize_arbitrum_tx_parses_wei_as_int() -> None:
    watched = {"0xabc"}
    tx = {
        "hash": "0xhash",
        "block_time": "2026-07-01T12:00:00+00:00",
        "from": "0xabc",
        "to": "0xdef",
        "value": "1000000000000000000",
    }
    transfers = transfers_from_arbitrum_tx(tx, watched=watched, source="test")
    assert transfers[0].amount == pytest.approx(1.0)


def test_watched_address_prefers_directional_side() -> None:
    inflow = WalletTransfer(
        chain="arbitrum",
        tx_hash="0xhash",
        transfer_index=1,
        block_time=datetime.now(tz=UTC),
        from_address="0xsender",
        to_address="0xwatched",
        asset_mint_or_contract="native",
        asset_symbol="ETH",
        amount=1.0,
        amount_usd=None,
        direction="inflow",
        program_or_method="transfer",
        source="test",
    )
    assert watched_address(inflow) == "0xwatched"


def test_detect_alerts_arbitrum_inflow_with_both_addresses() -> None:
    now = datetime.now(tz=UTC)
    registry = {
        ("0xwatched", "arbitrum"): WalletRegistryEntry(
            address="0xwatched",
            chain="arbitrum",
            label="Whale",
            entity_type="whale",
            confidence=0.8,
            source="manual",
            discovered_at=now,
            active=True,
        )
    }
    transfers = [
        WalletTransfer(
            chain="arbitrum",
            tx_hash="0xhash",
            transfer_index=1,
            block_time=now,
            from_address="0xsender",
            to_address="0xwatched",
            asset_mint_or_contract="native",
            asset_symbol="ETH",
            amount=1.0,
            amount_usd=None,
            direction="inflow",
            program_or_method="transfer",
            source="test",
        )
    ]
    alerts = detect_alerts(transfers, registry, since=now)
    assert len(alerts) == 1
    assert alerts[0].address == "0xwatched"


def test_normalize_solarchive_row() -> None:
    watched = {"WalletA"}
    row = {
        "signature": "sig",
        "block_time": "2026-07-01T12:00:00+00:00",
        "account_keys": ["WalletA", "WalletB"],
        "fee": 5000,
    }
    transfers = transfers_from_solarchive_row(row, watched=watched)
    assert len(transfers) == 1
    assert transfers[0].source == "solarchive"


def test_match_holder_amount() -> None:
    holders = [
        ("wallet1", 49_995_519),
        ("wallet2", 1_000_000),
    ]
    matched = match_holder_amount(holders, target_amount=49_995_519)
    assert matched == "wallet1"


def test_score_wallet_performance() -> None:
    confidence, entity = score_wallet_performance(
        win_rate=0.45,
        trade_count=120,
        median_hold_hours=3.0,
        min_win_rate=0.35,
    )
    assert entity == "smart_money"
    assert confidence > 0.7


def test_detect_alerts() -> None:
    now = datetime.now(tz=UTC)
    registry = {
        ("kol1", "solana"): WalletRegistryEntry(
            address="kol1",
            chain="solana",
            label="KOL",
            entity_type="kol",
            confidence=0.8,
            source="manual",
            discovered_at=now,
            active=True,
        )
    }
    transfers = [
        WalletTransfer(
            chain="solana",
            tx_hash="sig",
            transfer_index=0,
            block_time=now,
            from_address="",
            to_address="kol1",
            asset_mint_or_contract="mint",
            asset_symbol="TOKEN",
            amount=100.0,
            amount_usd=None,
            direction="inflow",
            program_or_method="spl-token",
            source="test",
        )
    ]
    alerts = detect_alerts(transfers, registry, since=now)
    assert len(alerts) == 1
    assert alerts[0].severity == "high"


def test_wallet_alerts_storage(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        now = datetime.now(tz=UTC)
        alert = WalletAlert(
            address="kol1",
            chain="solana",
            mint_or_contract="mint",
            action="kol_buy_TOKEN",
            severity="high",
            block_time=now,
            tx_hash="sig",
            alerted_at=now,
        )
        assert store.upsert_wallet_alerts([alert]) == 1
        rows = store.wallet_alerts_since(now)
        assert len(rows) == 1
    finally:
        store.close()
