from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ccquant.models import (
    WalletAlert,
    WalletIdentity,
    WalletIdentityLink,
    WalletRegistryEntry,
    WalletTransfer,
)
from ccquant.storage import MarketStore
from ccquant.wallet.alerts import _meets_alert_threshold, detect_alerts
from ccquant.wallet.discovery import match_holder_amount, score_wallet_performance
from ccquant.wallet.extract_bigquery import (
    build_arbitrum_bigquery_sql,
    build_bitcoin_bigquery_sql,
    build_solana_bigquery_sql,
)
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
    assert len(entries) >= 37
    chains = {entry.chain for entry in entries}
    assert "solana" in chains
    assert "arbitrum" in chains
    assert "bitcoin" in chains
    bitcoin_entries = [e for e in entries if e.chain == "bitcoin"]
    assert len(bitcoin_entries) >= 15


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


def test_normalize_arbitrum_tx_parses_float_wei() -> None:
    watched = {"0xabc"}
    tx = {
        "hash": "0xhash",
        "block_time": "2026-07-01T12:00:00+00:00",
        "from": "0xabc",
        "to": "0xdef",
        "value": 1e18,
    }
    transfers = transfers_from_arbitrum_tx(tx, watched=watched, source="test")
    assert transfers[0].amount == pytest.approx(1.0)


def test_bigquery_sql_escapes_address_quotes() -> None:
    from datetime import date

    addresses = ["addr'with'quote"]
    sql = build_solana_bigquery_sql(
        addresses,
        start=date(2026, 7, 1),
        end=date(2026, 7, 2),
    )
    assert "addr''with''quote" in sql
    arb_sql = build_arbitrum_bigquery_sql(
        addresses,
        start=date(2026, 7, 1),
        end=date(2026, 7, 2),
    )
    assert "addr''with''quote" in arb_sql


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


def test_history_complete_ignores_non_history_sources(tmp_path) -> None:
    from dataclasses import replace

    from ccquant.config import load_config
    from ccquant.models import WalletSyncState
    from ccquant.wallet.sync import WalletSync

    cfg = replace(load_config(), database=tmp_path / "ccquant.duckdb")
    store = MarketStore(cfg.database)
    try:
        now = datetime.now(tz=UTC)
        entry = WalletRegistryEntry(
            address="WalletA",
            chain="solana",
            label="Test",
            entity_type="kol",
            confidence=0.8,
            source="manual",
            discovered_at=now,
            active=True,
        )
        store.upsert_wallet_registry([entry])
        store.upsert_wallet_sync_state(
            WalletSyncState(
                address="WalletA",
                chain="solana",
                source="history",
                backfill_complete=True,
                earliest_at=now,
                latest_at=now,
                last_refresh_at=now,
            )
        )
        store.upsert_wallet_sync_state(
            WalletSyncState(
                address="WalletA",
                chain="solana",
                source="rpc_tail",
                backfill_complete=False,
                earliest_at=now,
                latest_at=now,
                last_refresh_at=now,
            )
        )
        syncer = WalletSync(store, cfg)
        assert syncer._history_complete() is True
    finally:
        store.close()


def test_upsert_wallet_identities_and_links(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        linked_at = datetime(2024, 6, 1, 12, 0)
        identities = [
            WalletIdentity(
                identity_id="strategy",
                display_name="MicroStrategy",
                category="corporate",
                description="",
                source_url="",
                active=True,
            )
        ]
        links = [
            WalletIdentityLink(
                address="1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
                chain="bitcoin",
                identity_id="strategy",
                link_type="owns",
                confidence=0.9,
                source="manual",
                linked_at=linked_at,
            ),
            WalletIdentityLink(
                address="bc1qjasf9z3h7l3jkaware86a4s4ut9t928cerovd",
                chain="bitcoin",
                identity_id="strategy",
                link_type="owns",
                confidence=0.85,
                source="manual",
                linked_at=linked_at,
            ),
        ]
        assert store.upsert_wallet_identities(identities) == 1
        assert store.upsert_wallet_identity_links(links) == 2
        row = store.connection.execute(
            "select count(*) from wallet_identity_links where identity_id = 'strategy'"
        ).fetchone()
        assert row is not None
        assert int(row[0]) == 2

        updated = WalletIdentityLink(
            address="1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
            chain="bitcoin",
            identity_id="strategy",
            link_type="owns",
            confidence=0.99,
            source="manual",
            linked_at=datetime(2025, 6, 1),
        )
        store.upsert_wallet_identity_links([updated])
        preserved = store.connection.execute(
            """
            select linked_at, confidence
            from wallet_identity_links
            where address = ? and chain = 'bitcoin' and identity_id = 'strategy'
            """,
            ["1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"],
        ).fetchone()
        assert preserved is not None
        assert preserved[0] == linked_at
        assert float(preserved[1]) == pytest.approx(0.99)
    finally:
        store.close()


def test_load_seed_identity_links_parses_linked_at(tmp_path) -> None:
    from ccquant.wallet.seeds import load_seed_identity_links

    seed = tmp_path / "links.csv"
    seed.write_text(
        "address,chain,identity_id,link_type,confidence,source,linked_at\n"
        "bc1qtest,bitcoin,strategy,owns,0.9,manual,2024-03-15T12:00:00+00:00\n",
        encoding="utf-8",
    )
    links = load_seed_identity_links(seed)
    assert len(links) == 1
    assert links[0].linked_at == datetime(2024, 3, 15, 12, tzinfo=UTC)


def test_load_seed_identity_links_rejects_invalid_linked_at(tmp_path) -> None:
    from ccquant.wallet.seeds import load_seed_identity_links

    seed = tmp_path / "links.csv"
    seed.write_text(
        "address,chain,identity_id,link_type,confidence,source,linked_at\n"
        "bc1qtest,bitcoin,strategy,owns,0.9,manual,not-a-timestamp\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid linked_at"):
        load_seed_identity_links(seed)


def test_rows_to_transfers_bitcoin_keeps_outflow_for_watched_address() -> None:
    from ccquant.wallet.extract_bigquery import rows_to_transfers

    watched = {"1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"}
    rows = [
        {
            "hash": "tx1",
            "block_time": datetime(2024, 1, 1, tzinfo=UTC),
            "leg_index": 0,
            "address": "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
            "value_sats": 100_000_000,
            "script_type": "p2pkh",
            "direction": "outflow",
            "counterparty": "34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi",
        }
    ]
    transfers = rows_to_transfers(rows, chain="bitcoin", watched=watched)
    assert len(transfers) == 1
    assert transfers[0].direction == "outflow"
    assert transfers[0].from_address == "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"
    assert transfers[0].to_address == "34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi"


def test_build_bitcoin_bigquery_sql_filters_addresses() -> None:
    from datetime import date

    sql = build_bitcoin_bigquery_sql(
        ["1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 7),
    )
    assert "crypto_bitcoin.transactions" in sql
    assert "1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj" in sql
    assert "unnest(t.outputs) as output with offset as output_offset," in sql
    assert "unnest(output.addresses) as addr" in sql
    assert "unnest(t.inputs) as input with offset as input_offset," in sql
    assert "unnest(input.addresses) as addr" in sql
    assert "counterparty" in sql
    assert "timestamp('2024-01-08')" in sql
    assert "between timestamp('2024-01-01') and timestamp('2024-01-07')" not in sql


def test_bitcoin_alert_threshold() -> None:
    base = dict(
        chain="bitcoin",
        tx_hash="tx",
        transfer_index=0,
        block_time=datetime.now(tz=UTC),
        from_address="a",
        to_address="b",
        asset_mint_or_contract="btc",
        asset_symbol="BTC",
        direction="inflow",
        program_or_method="p2wpkh",
        source="test",
    )
    assert not _meets_alert_threshold(
        WalletTransfer(amount=5.0, amount_usd=None, **base)
    )
    assert _meets_alert_threshold(
        WalletTransfer(amount=10.0, amount_usd=None, **base)
    )
    assert _meets_alert_threshold(
        WalletTransfer(amount=1.0, amount_usd=100_000.0, **base)
    )
    assert not _meets_alert_threshold(
        WalletTransfer(amount=1.0, amount_usd=50_000.0, **base)
    )


@pytest.mark.asyncio
async def test_fetch_signatures_returns_empty_on_http_error() -> None:
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from ccquant.wallet.tail_solana import _fetch_signatures

    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.HTTPError("boom"))
    sigs = await _fetch_signatures(
        client,
        rpc_url="http://rpc",
        address="WalletA",
        before=None,
        limit=5,
    )
    assert sigs == []


@pytest.mark.asyncio
async def test_sync_all_no_tail_skips_history_and_tail(tmp_path) -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock, patch

    from ccquant.config import load_config
    from ccquant.wallet.sync import WalletSync

    cfg = replace(load_config(), database=tmp_path / "ccquant.duckdb")
    store = MarketStore(cfg.database)
    syncer = WalletSync(store, cfg)
    try:
        with (
            patch.object(
                syncer,
                "backfill_history",
                new=AsyncMock(return_value=99),
            ) as backfill,
            patch.object(
                syncer,
                "tail_refresh",
                new=AsyncMock(return_value=88),
            ) as tail,
        ):
            counts = await syncer.sync_all(full=False, tail=False, history=False)
        assert "registry" in counts
        assert "history" not in counts
        assert "tail" not in counts
        backfill.assert_not_called()
        tail.assert_not_called()
    finally:
        await syncer.close()
        store.close()


@pytest.mark.asyncio
async def test_tail_refresh_skips_bitcoin_when_chain_disabled(tmp_path) -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock, patch

    from ccquant.config import load_config
    from ccquant.wallet.sync import WalletSync

    base_cfg = load_config()
    cfg = replace(
        base_cfg,
        database=tmp_path / "ccquant.duckdb",
        wallet_tracking=replace(
            base_cfg.wallet_tracking,
            chains=["solana", "arbitrum"],
        ),
    )
    store = MarketStore(cfg.database)
    now = datetime.now(tz=UTC)
    store.upsert_wallet_registry(
        [
            WalletRegistryEntry(
                address="1NDyJtNTjmwk5xPNe21PaRLLJ46W4hKEMj",
                chain="bitcoin",
                label="BTC Wallet",
                entity_type="whale",
                confidence=0.9,
                source="manual",
                discovered_at=now,
                active=True,
            )
        ]
    )
    syncer = WalletSync(store, cfg)
    try:
        with (
            patch(
                "ccquant.wallet.sync.tail_solana_wallets",
                AsyncMock(return_value=[]),
            ) as tail_solana,
            patch(
                "ccquant.wallet.sync.tail_arbitrum_wallets",
                AsyncMock(return_value=[]),
            ) as tail_arbitrum,
            patch(
                "ccquant.wallet.sync.tail_bitcoin_wallets",
                AsyncMock(return_value=[]),
            ) as tail_bitcoin,
        ):
            await syncer.tail_refresh()
        tail_solana.assert_not_called()
        tail_arbitrum.assert_not_called()
        tail_bitcoin.assert_not_called()
    finally:
        await syncer.close()
        store.close()


@pytest.mark.asyncio
async def test_backfill_solarchive_skips_missing_partitions(tmp_path) -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock, patch

    from ccquant.config import load_config
    from ccquant.wallet.extract_solarchive import SolArchivePartitionNotFoundError
    from ccquant.wallet.sync import WalletSync

    cfg = replace(load_config(), database=tmp_path / "ccquant.duckdb")
    store = MarketStore(cfg.database)
    now = datetime.now(tz=UTC)
    store.upsert_wallet_registry(
        [
            WalletRegistryEntry(
                address="abc123",
                chain="solana",
                label="Test",
                entity_type="kol",
                confidence=0.8,
                source="manual",
                discovered_at=now,
                active=True,
            )
        ]
    )
    syncer = WalletSync(store, cfg)
    try:
        fetch = AsyncMock(
            side_effect=SolArchivePartitionNotFoundError("missing partition")
        )
        with patch(
            "ccquant.wallet.sync.fetch_partition_index",
            fetch,
        ):
            total = await syncer._backfill_solarchive()
        assert total == 0
        assert fetch.await_count > 0
    finally:
        await syncer.close()
        store.close()


@pytest.mark.asyncio
async def test_discover_skips_when_flipside_disabled(tmp_path) -> None:
    from dataclasses import replace

    from ccquant.config import load_config
    from ccquant.wallet.sync import WalletSync

    base = load_config()
    cfg = replace(
        base,
        database=tmp_path / "ccquant.duckdb",
        wallet_tracking=replace(
            base.wallet_tracking,
            discovery=replace(
                base.wallet_tracking.discovery,
                flipside_enabled=False,
            ),
        ),
    )
    store = MarketStore(cfg.database)
    syncer = WalletSync(store, cfg)
    try:
        count = await syncer.discover(chain="solana", top=5)
        assert count == 0
    finally:
        await syncer.close()
        store.close()
