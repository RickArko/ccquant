"""Tests for credit-safe sync bootstrap CLI."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from ccquant.cli import app
from ccquant.models import Asset, SyncState
from ccquant.storage import MarketStore

runner = CliRunner()


def _seed_complete_db(path: Path) -> None:
    store = MarketStore(path)
    try:
        as_of = date(2026, 7, 2)
        store.replace_assets(
            [
                Asset(
                    rank=1,
                    symbol="BTC",
                    coingecko_id="bitcoin",
                    binance_pair="BTCUSDT",
                    coinbase_product_id="BTC-USD",
                    active=True,
                    as_of_date=as_of,
                )
            ],
            as_of,
        )
        store.upsert_state(
            SyncState(
                symbol="BTC",
                interval="1d",
                backfill_complete=True,
                earliest_at=datetime(2020, 1, 1, tzinfo=UTC),
                latest_at=datetime(2026, 7, 1, tzinfo=UTC),
                last_refresh_at=datetime(2026, 7, 2, tzinfo=UTC),
            )
        )
    finally:
        store.close()


def test_bootstrap_requires_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["sync", "bootstrap", "--no-dbt"])
    assert result.exit_code == 1
    assert "exactly one" in result.stdout


def test_bootstrap_dry_run_cold_prints_plan_and_skips_work(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "data" / "ccquant.duckdb"
    monkeypatch.setenv("CCQUANT_DB", str(db))
    monkeypatch.chdir(tmp_path)
    with (
        patch("ccquant.cli.MarketSync") as mock_sync,
        patch("ccquant.cli.MarketStore.restore") as mock_restore,
    ):
        result = runner.invoke(
            app,
            ["sync", "bootstrap", "--cold", "--dry-run", "--no-dbt"],
        )
    assert result.exit_code == 0
    assert "Bootstrap plan" in result.stdout
    assert "[SKIP] onchain BID valuation" in result.stdout
    assert "[SKIP] wallets" in result.stdout
    assert "Dry run" in result.stdout
    mock_sync.assert_not_called()
    mock_restore.assert_not_called()


def test_bootstrap_cold_refuses_when_backfill_complete(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "ccquant.duckdb"
    _seed_complete_db(db)
    monkeypatch.setenv("CCQUANT_DB", str(db))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["sync", "bootstrap", "--cold", "--no-dbt"]
    )
    assert result.exit_code == 1
    assert "already complete" in result.stdout


def test_bootstrap_from_backup_dry_run(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "backup.duckdb"
    _seed_complete_db(source)
    dest = tmp_path / "data" / "ccquant.duckdb"
    monkeypatch.setenv("CCQUANT_DB", str(dest))
    monkeypatch.chdir(tmp_path)
    with patch("ccquant.cli.MarketStore.restore") as mock_restore:
        result = runner.invoke(
            app,
            [
                "sync",
                "bootstrap",
                "--from-backup",
                str(source),
                "--dry-run",
                "--no-dbt",
            ],
        )
    assert result.exit_code == 0
    assert "restore" in result.stdout.lower()
    mock_restore.assert_not_called()


def test_bootstrap_restore_path_skips_bid_and_wallets_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "backup.duckdb"
    _seed_complete_db(source)
    dest = tmp_path / "ccquant.duckdb"
    monkeypatch.setenv("CCQUANT_DB", str(dest))
    monkeypatch.chdir(tmp_path)

    mock_instance = MagicMock()
    mock_instance.update_universe = AsyncMock(return_value=1)
    mock_instance.backfill = AsyncMock(return_value={"BTC": 0})
    mock_instance.backfill_oi_all = AsyncMock(return_value={"BTC": 0})
    mock_instance.sync_order_book_all = AsyncMock(return_value={"BTC": 0})
    mock_instance.sync_mev = AsyncMock(return_value={"BTC": 0})
    mock_instance.backfill_macro = AsyncMock(return_value={"M2SL": 0})
    mock_instance.close = AsyncMock()
    mock_instance.sync_onchain = MagicMock(
        return_value={"blockchain.info": 0, "bitcoinisdata": 0}
    )
    mock_instance.sync_etf_mstr = MagicMock(
        return_value={"etf_flows": 0, "mstr": 0}
    )

    with (
        patch("ccquant.cli.MarketSync", return_value=mock_instance),
        patch("ccquant.cli.WalletSync") as wallet_cls,
        patch("ccquant.cli._run_dbt", return_value=False),
    ):
        result = runner.invoke(
            app,
            [
                "sync",
                "bootstrap",
                "--from-backup",
                str(source),
                "--force-restore",
                "--no-dbt",
            ],
        )
    assert result.exit_code == 0, result.stdout
    mock_instance.sync_onchain.assert_called()
    # allow_bid must be False by default
    kwargs = mock_instance.sync_onchain.call_args.kwargs
    assert kwargs.get("allow_bid") is False
    wallet_cls.assert_not_called()
