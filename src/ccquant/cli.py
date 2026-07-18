from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ccquant.config import AppConfig, load_config
from ccquant.storage import MarketStore
from ccquant.sync import MarketSync
from ccquant.twitter import TwitterSync
from ccquant.wallet import WalletSync

app = typer.Typer(help="Crypto OHLCV data and forecasting research toolkit")
sync_app = typer.Typer(help="Fetch and refresh market data")
export_app = typer.Typer(help="Export DuckDB tables")
migrate_app = typer.Typer(help="Migrate data between databases")
import_app = typer.Typer(help="Import external datasets into DuckDB")
db_app = typer.Typer(help="Database backup and maintenance")
wallet_app = typer.Typer(help="Wallet intelligence and on-chain tracking")
twitter_app = typer.Typer(help="Twitter / X tweet tracking (import-only)")
research_app = typer.Typer(help="Strategy research / walk-forward evaluation")
app.add_typer(sync_app, name="sync")
app.add_typer(export_app, name="export")
app.add_typer(migrate_app, name="migrate")
app.add_typer(import_app, name="import")
app.add_typer(db_app, name="db")
app.add_typer(wallet_app, name="wallet")
app.add_typer(twitter_app, name="twitter")
app.add_typer(research_app, name="research")
console = Console()


DEFAULT_EXPORT_DIR = Path("data/export")
DBT_PROJECT_DIR = Path("dbt")
DBT_PROFILES_DIR = Path("dbt")


def _load(config: str | None) -> tuple[MarketStore, AppConfig]:
    cfg = load_config(config)
    return MarketStore(cfg.database), cfg


def _run_dbt(command: str, *args: str) -> bool:
    """Run a dbt subcommand. Returns True on success, False if dbt not found."""
    dbt_bin = shutil.which("dbt")
    if dbt_bin is None:
        console.print(
            "[yellow]dbt not installed — skipping dbt step.[/yellow]"
            " Install with: uv sync --extra dbt"
        )
        return False
    cmd = [
        dbt_bin,
        command,
        *args,
        "--project-dir",
        str(DBT_PROJECT_DIR),
        "--profiles-dir",
        str(DBT_PROFILES_DIR),
    ]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, check=False)
    return result.returncode == 0


@sync_app.command("universe")
def sync_universe(
    config: str | None = typer.Option(None, "--config", "-c"),
    size: int | None = typer.Option(None, "--size", help="Override universe size"),
) -> None:
    """Fetch top market-cap universe and probe available exchange pairs."""
    store, cfg = _load(config)
    syncer = MarketSync(store, cfg)

    async def run() -> int:
        try:
            return await syncer.update_universe(size=size)
        finally:
            await syncer.close()
            store.close()

    count = asyncio.run(run())
    console.print(f"[green]Universe updated: {count} assets[/green]")


@sync_app.command("backfill")
def sync_backfill(
    config: str | None = typer.Option(None, "--config", "-c"),
    interval: Annotated[
        str,
        typer.Option("--interval", "-i", help="1d or 1h"),
    ] = "1d",
    full: bool = typer.Option(True, "--full/--tail"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Ignore sync_state.backfill_complete and re-pull full history",
    ),
    top: int | None = typer.Option(None, "--top", help="Limit to top-N ranked assets"),
) -> None:
    """Backfill or tail-refresh OHLCV data."""
    if interval not in {"1d", "1h"}:
        raise typer.BadParameter("interval must be 1d or 1h")
    if force and not full:
        raise typer.BadParameter("--force requires --full (omit --tail)")
    store, cfg = _load(config)
    syncer = MarketSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.backfill(
                interval=interval, full=full, top=top, force=force
            )
        finally:
            await syncer.close()
            store.close()

    results = asyncio.run(run())
    total = sum(results.values())
    console.print(f"[green]{interval} sync complete: {total} candles[/green]")
    for symbol, count in sorted(results.items()):
        console.print(f"  {symbol}: {count}")


@sync_app.command("oi")
def sync_oi(
    config: str | None = typer.Option(None, "--config", "-c"),
    interval: Annotated[
        str,
        typer.Option("--interval", "-i", help="1d or 1h"),
    ] = "1h",
    full: bool = typer.Option(False, "--full/--tail"),
    top: int | None = typer.Option(None, "--top", help="Limit to top-N ranked assets"),
) -> None:
    """Backfill or tail-refresh open interest data."""
    if interval not in {"1d", "1h"}:
        raise typer.BadParameter("interval must be 1d or 1h")
    store, cfg = _load(config)
    syncer = MarketSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.backfill_oi_all(
                interval=interval, full=full, top=top
            )
        finally:
            await syncer.close()
            store.close()

    results = asyncio.run(run())
    total = sum(results.values())
    console.print(f"[green]OI sync complete: {total} data points[/green]")
    for symbol, count in sorted(results.items()):
        if count:
            console.print(f"  {symbol}: {count}")


@sync_app.command("macro")
def sync_macro(
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Fetch FRED macro series into the database."""
    store, cfg = _load(config)
    syncer = MarketSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.backfill_macro()
        finally:
            await syncer.close()
            store.close()

    results = asyncio.run(run())
    total = sum(results.values())
    console.print(f"[green]Macro sync complete: {total} data points[/green]")
    for series_id, count in sorted(results.items()):
        console.print(f"  {series_id}: {count}")


@sync_app.command("depth")
def sync_depth(
    config: str | None = typer.Option(None, "--config", "-c"),
    top: int | None = typer.Option(
        None, "--top", help="Limit to top-N ranked assets"
    ),
) -> None:
    """Poll free CEX order-book depth and store bps-band volume features."""
    store, cfg = _load(config)
    syncer = MarketSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.sync_order_book_all(top=top)
        finally:
            await syncer.close()
            store.close()

    results = asyncio.run(run())
    total = sum(results.values())
    console.print(f"[green]Depth sync complete: {total} snapshots[/green]")
    for symbol, count in sorted(results.items()):
        if count:
            console.print(f"  {symbol}: {count}")


@sync_app.command("mev")
def sync_mev(
    config: str | None = typer.Option(None, "--config", "-c"),
    top: int | None = typer.Option(
        None, "--top", help="Limit DEX price fetch to top-N assets"
    ),
) -> None:
    """Sync DEX USD prices and import local MEV-Boost parquet if present."""
    store, cfg = _load(config)
    syncer = MarketSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.sync_mev(top=top)
        finally:
            await syncer.close()
            store.close()

    results = asyncio.run(run())
    console.print("[green]MEV sync complete[/green]")
    for key, count in sorted(results.items()):
        console.print(f"  {key}: {count}")


@sync_app.command("wallets")
def sync_wallets(
    config: str | None = typer.Option(None, "--config", "-c"),
    full: bool = typer.Option(False, "--full/--no-full"),
    no_tail: bool = typer.Option(
        False,
        "--no-tail",
        help="Registry only: skip history backfill and RPC tail refresh",
    ),
) -> None:
    """Sync wallet registry, historical extracts, and tail activity."""
    store, cfg = _load(config)
    syncer = WalletSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.sync_all(
                full=full,
                tail=not no_tail,
                history=not no_tail,
            )
        finally:
            await syncer.close()

    results = asyncio.run(run())
    counts = store.wallet_row_counts()
    console.print("[green]Wallet sync complete[/green]")
    for key, value in results.items():
        console.print(f"  {key}: {value}")
    console.print(
        f"  registry rows: {counts.get('wallet_registry', 0)}, "
        f"transfer rows: {counts.get('wallet_transfers', 0)}"
    )
    store.close()


@sync_app.command("tweets")
def sync_tweets(
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Import tweets from inbox CSV/JSONL, enrich, and aggregate signals."""
    store, cfg = _load(config)
    syncer = TwitterSync(store, cfg)
    results = syncer.sync_all()
    counts = store.twitter_row_counts()
    console.print("[green]Tweet sync complete[/green]")
    for key, value in results.items():
        console.print(f"  {key}: {value}")
    console.print(
        f"  accounts: {counts.get('twitter_accounts', 0)}, "
        f"tweets: {counts.get('tweets', 0)}, "
        f"entities: {counts.get('tweet_entities', 0)}"
    )
    store.close()


@sync_app.command("all")
def sync_all(
    config: str | None = typer.Option(None, "--config", "-c"),
    size: int | None = typer.Option(None, "--size", help="Override universe size"),
    hourly_top: int | None = typer.Option(
        None, "--hourly-top", help="Limit hourly to top-N assets"
    ),
    oi_interval: Annotated[
        str,
        typer.Option("--oi-interval", help="Open interest interval: 1d or 1h"),
    ] = "1h",
    dbt: bool = typer.Option(
        True,
        "--dbt/--no-dbt",
        help="Run dbt snapshot + build + test after Python sync",
    ),
    wallets: bool = typer.Option(
        True, "--wallets/--no-wallets", help="Run wallet intelligence sync"
    ),
    tweets: bool = typer.Option(
        True, "--tweets/--no-tweets", help="Run tweet import sync"
    ),
    depth: bool = typer.Option(
        True, "--depth/--no-depth", help="Poll CEX order-book depth"
    ),
    mev: bool = typer.Option(
        True, "--mev/--no-mev", help="Sync DEX prices + local MEV-Boost import"
    ),
) -> None:
    """Sync universe, OHLCV, OI, depth, MEV, macro, wallets, tweets, then dbt.

    One command to bring the local DB up to today. Runs:
    1. Universe refresh (top-cap rankings + exchange pair probes)
    2. Daily tail-refresh (full backfill already complete)
    3. Hourly tail-refresh (top-N assets)
    4. Open interest tail-refresh (Binance + Bybit + OKX, per config toggles)
    5. Order-book depth snapshots (bps-band volume features)
    6. MEV sync (DEX prices + optional local MEV-Boost parquet)
    7. Macro sync (FRED series, if FRED_API_KEY is set)
    8. Wallet intelligence sync (registry + history + tail)
    9. Tweet import sync (inbox CSV/JSONL)
    10. dbt snapshot (SCD2 snap_assets) then dbt build + test
    11. Status table
    """
    store, cfg = _load(config)
    try:
        syncer = MarketSync(store, cfg)

        async def run() -> None:
            try:
                # 1. Universe
                count = await syncer.update_universe(size=size)
                console.print(f"[green]Universe updated: {count} assets[/green]")

                # 2. Daily tail-refresh (full=True auto-falls
                #    back to tail when backfill_complete)
                console.print("\n[dim]Daily tail-refresh...[/dim]")
                daily = await syncer.backfill(interval="1d", full=True)
                console.print(f"[green]Daily: {sum(daily.values())} candles[/green]")

                # 3. Hourly tail-refresh
                top = hourly_top if hourly_top is not None else cfg.hourly.top
                console.print(f"\n[dim]Hourly tail-refresh (top {top})...[/dim]")
                hourly = await syncer.backfill(interval="1h", full=False, top=top)
                console.print(f"[green]Hourly: {sum(hourly.values())} candles[/green]")

                # 4. Open interest tail-refresh
                if cfg.open_interest.enabled:
                    console.print(
                        f"\n[dim]Open interest ({oi_interval})...[/dim]"
                    )
                    oi = await syncer.backfill_oi_all(
                        interval=oi_interval, full=False, top=top
                    )
                    oi_total = sum(oi.values())
                    console.print(
                        f"[green]OI: {oi_total} data points[/green]"
                    )

                # 5. Order-book depth
                if depth and cfg.order_book.enabled:
                    depth_top = cfg.order_book.top
                    console.print(
                        f"\n[dim]Order-book depth (top {depth_top})...[/dim]"
                    )
                    depth_results = await syncer.sync_order_book_all(
                        top=depth_top
                    )
                    console.print(
                        f"[green]Depth: {sum(depth_results.values())} "
                        f"snapshots[/green]"
                    )

                # 6. MEV (DEX prices + local boost parquet)
                if mev and cfg.mev.enabled:
                    console.print("\n[dim]MEV (DEX prices / boost)...[/dim]")
                    mev_results = await syncer.sync_mev(top=cfg.order_book.top)
                    console.print(
                        f"[green]MEV: {sum(mev_results.values())} rows[/green]"
                    )

                # 7. Macro sync (FRED)
                if cfg.macro.enabled:
                    console.print("\n[dim]Macro (FRED)...[/dim]")
                    macro = await syncer.backfill_macro()
                    macro_total = sum(macro.values())
                    console.print(
                        f"[green]Macro: {macro_total} data points[/green]"
                    )
            finally:
                await syncer.close()

        asyncio.run(run())

        # 6. Wallet intelligence
        if wallets and cfg.wallet_tracking.enabled:
            console.print("\n[dim]Wallet intelligence...[/dim]")
            wallet_syncer = WalletSync(store, cfg)

            async def run_wallets() -> dict[str, int]:
                try:
                    return await wallet_syncer.sync_all(full=False, tail=True)
                finally:
                    await wallet_syncer.close()

            wallet_results = asyncio.run(run_wallets())
            console.print(
                f"[green]Wallets: {sum(wallet_results.values())} operations[/green]"
            )

        # 7. Tweet import
        if tweets and cfg.twitter_tracking.enabled:
            console.print("\n[dim]Tweet import...[/dim]")
            tweet_syncer = TwitterSync(store, cfg)
            tweet_results = tweet_syncer.sync_all()
            console.print(
                f"[green]Tweets: {tweet_results.get('imported', 0)} imported[/green]"
            )
    finally:
        # Release the Python write lock before dbt opens the same DuckDB file.
        # DuckDB allows only one writer; keeping MarketStore open causes:
        # "Conflicting lock is held in ... python3.13".
        store.close()

    # 8. dbt snapshot + build + test
    if dbt:
        console.print("\n[dim]dbt snapshot...[/dim]")
        snap_ok = _run_dbt("snapshot")
        if snap_ok:
            console.print("[green]dbt snapshot: PASS[/green]")
        else:
            console.print("[yellow]dbt snapshot: skipped or failed[/yellow]")
        console.print("\n[dim]dbt build...[/dim]")
        dbt_ok = _run_dbt("build")
        if dbt_ok:
            console.print("[green]dbt build: PASS[/green]")
        else:
            console.print("[yellow]dbt build: skipped or failed[/yellow]")

    # 9. Status (re-open after dbt releases its lock)
    store, _cfg = _load(config)
    try:
        console.print()
        table = Table(title="ccquant data status")
        for col in [
            "symbol", "rank", "daily rows", "daily range",
            "hourly rows", "hourly range",
        ]:
            table.add_column(col)
        for row in store.status_rows():
            table.add_row(
                str(row["symbol"]),
                str(row["rank"]),
                str(row["daily_rows"]),
                f"{row['daily_from'] or '-'} -> {row['daily_to'] or '-'}",
                str(row["hourly_rows"]),
                f"{row['hourly_from'] or '-'} -> {row['hourly_to'] or '-'}",
            )
        console.print(table)
    finally:
        store.close()


@app.command("status")
def status(config: str | None = typer.Option(None, "--config", "-c")) -> None:
    """Show stored row counts and date ranges."""
    store, _cfg = _load(config)
    try:
        table = Table(title="ccquant data status")
        for col in [
            "symbol",
            "rank",
            "daily rows",
            "daily range",
            "hourly rows",
            "hourly range",
        ]:
            table.add_column(col)
        for row in store.status_rows():
            table.add_row(
                str(row["symbol"]),
                str(row["rank"]),
                str(row["daily_rows"]),
                f"{row['daily_from'] or '-'} -> {row['daily_to'] or '-'}",
                str(row["hourly_rows"]),
                f"{row['hourly_from'] or '-'} -> {row['hourly_to'] or '-'}",
            )
        console.print(table)
    finally:
        store.close()


@export_app.command("parquet")
def export_parquet(
    config: str | None = typer.Option(None, "--config", "-c"),
    out: Annotated[Path, typer.Option("--out")] = DEFAULT_EXPORT_DIR,
) -> None:
    """Export core tables as Parquet."""
    _export(config, out, fmt="parquet")


@export_app.command("csv")
def export_csv(
    config: str | None = typer.Option(None, "--config", "-c"),
    out: Annotated[Path, typer.Option("--out")] = DEFAULT_EXPORT_DIR,
) -> None:
    """Export core tables as CSV."""
    _export(config, out, fmt="csv")


def _export(config: str | None, out: Path, *, fmt: str) -> None:
    store, _cfg = _load(config)
    try:
        for table in [
            "assets",
            "ohlcv_daily",
            "ohlcv_hourly",
            "sync_state",
            "onchain_series",
            "onchain_sync_state",
            "open_interest",
            "order_book_snapshots",
            "order_book_sync_state",
            "dex_price_daily",
            "mev_boost_payloads",
            "macro_series",
            "macro_sync_state",
            "wallet_registry",
            "wallet_transfers",
            "wallet_positions_daily",
            "wallet_sync_state",
            "wallet_alerts",
            "wallet_identities",
            "wallet_identity_links",
            "twitter_accounts",
            "tweets",
            "tweet_entities",
            "tweet_sync_state",
            "tweet_signals_daily",
            "tweet_alerts",
        ]:
            path = store.export_table(table, out, fmt=fmt)
            console.print(f"[green]wrote[/green] {path}")
    finally:
        store.close()


@import_app.command("mev-boost")
def import_mev_boost(
    config: str | None = typer.Option(None, "--config", "-c"),
    source: str = typer.Option(
        ...,
        "--source",
        "-s",
        help="Parquet file or directory (e.g. data/mev/mevboost)",
    ),
    relay: str = typer.Option(
        "flashbots", "--relay", help="Default relay label when column missing"
    ),
) -> None:
    """Import MEV-Boost winning-bid parquet dumps into ``mev_boost_payloads``."""
    store, _cfg = _load(config)
    try:
        count = store.import_mev_boost_parquet(source, relay=relay)
        console.print(
            f"[green]Imported {count} MEV-Boost payload rows from {source}[/green]"
        )
    finally:
        store.close()


@migrate_app.command("onchain")
def migrate_onchain(
    config: str | None = typer.Option(None, "--config", "-c"),
    source: str = typer.Option(
        "data/onchain.duckdb", "--source", "-s", help="Source onchain DuckDB path"
    ),
) -> None:
    """Migrate on-chain series from a separate DuckDB into the main database."""
    store, _cfg = _load(config)
    try:
        counts = store.migrate_onchain(source)
        console.print(
            f"[green]Migrated onchain:[/green] "
            f"{counts['onchain_series']} series rows, "
            f"{counts['onchain_sync_state']} state rows"
        )
    finally:
        store.close()


@db_app.command("backup")
def db_backup(
    config: str | None = typer.Option(None, "--config", "-c"),
    dest: Annotated[Path, typer.Option("--dest")] = Path("data/backups"),
    keep: int = typer.Option(10, "--keep", help="Number of backups to retain"),
) -> None:
    """Create a timestamped file-copy backup of the DuckDB database."""
    store, _cfg = _load(config)
    try:
        path = store.backup(dest, keep=keep)
        console.print(f"[green]Backup created:[/green] {path}")
    finally:
        store.close()


@wallet_app.command("discover")
def wallet_discover(
    config: str | None = typer.Option(None, "--config", "-c"),
    chain: str = typer.Option(
        "solana",
        "--chain",
        help="solana, arbitrum, ethereum, or bitcoin",
    ),
    top: int = typer.Option(20, "--top", help="Number of wallets to discover"),
) -> None:
    """Discover labeled wallets from Flipside (or heuristic fallback)."""
    store, cfg = _load(config)
    syncer = WalletSync(store, cfg)

    async def run() -> int:
        try:
            return await syncer.discover(chain=chain.lower(), top=top)
        finally:
            await syncer.close()
            store.close()

    count = asyncio.run(run())
    console.print(f"[green]Discovered {count} wallets on {chain}[/green]")


@wallet_app.command("import-extract")
def wallet_import_extract(
    config: str | None = typer.Option(None, "--config", "-c"),
    source: str = typer.Option(
        "solarchive",
        "--source",
        help="solarchive or bigquery",
    ),
    chain: str = typer.Option(
        "solana",
        "--chain",
        help="solana, arbitrum, or bitcoin (bigquery only for bitcoin)",
    ),
    partition_date: str | None = typer.Option(
        None, "--date", help="Partition date YYYY-MM-DD for solarchive"
    ),
    parquet: Annotated[
        Path | None,
        typer.Option("--parquet", help="Local parquet path override"),
    ] = None,
) -> None:
    """Import a bounded open extract into wallet_transfers."""
    from datetime import date as date_type

    store, cfg = _load(config)
    syncer = WalletSync(store, cfg)
    parsed_date = (
        date_type.fromisoformat(partition_date) if partition_date else None
    )

    async def run() -> int:
        try:
            await syncer.load_registry()
            return await syncer.import_extract(
                source=source,
                chain=chain.lower(),
                partition_date=parsed_date,
                parquet_path=parquet,
            )
        finally:
            await syncer.close()

    try:
        count = asyncio.run(run())
    except Exception as exc:
        from ccquant.wallet.extract_solarchive import SolArchivePartitionNotFoundError

        if isinstance(exc, SolArchivePartitionNotFoundError):
            console.print(f"[yellow]{exc}[/yellow]")
            raise typer.Exit(code=1) from exc
        raise
    finally:
        store.close()

    console.print(f"[green]Imported {count} transfer rows from {source}[/green]")


@wallet_app.command("resolve-sns")
def wallet_resolve_sns(
    domain: str = typer.Argument(..., help="SNS domain, e.g. mitch.sol"),
) -> None:
    """Resolve a .sol SNS domain to a wallet address."""
    from ccquant.wallet.discovery import resolve_sns_domain

    async def run() -> str | None:
        async with httpx.AsyncClient() as client:
            return await resolve_sns_domain(client, domain)

    address = asyncio.run(run())
    if address:
        console.print(f"[green]{domain}[/green] -> {address}")
    else:
        console.print(f"[yellow]Could not resolve {domain}[/yellow]")


@wallet_app.command("match-holder")
def wallet_match_holder(
    mint: str = typer.Option(..., "--mint", help="Token mint address"),
    amount: float = typer.Option(..., "--amount", help="Exact holder balance"),
    holder: str = typer.Option(
        "",
        "--holder",
        help="Optional known holder address to verify",
    ),
) -> None:
    """Match a holder balance (screenshot trick) against a candidate address."""
    from ccquant.wallet.discovery import match_holder_amount

    holders = [(holder, amount)] if holder else [("candidate", amount)]
    matched = match_holder_amount(holders, target_amount=amount)
    if matched:
        console.print(f"[green]Matched holder:[/green] {matched}")
    else:
        console.print("[yellow]No holder matched the target amount[/yellow]")


@wallet_app.command("alerts")
def wallet_alerts(
    config: str | None = typer.Option(None, "--config", "-c"),
    since_hours: int = typer.Option(1, "--since", help="Hours to look back"),
) -> None:
    """Show recent wallet alerts from the local database."""
    from datetime import UTC, datetime, timedelta

    store, _cfg = _load(config)
    try:
        since = datetime.now(tz=UTC) - timedelta(hours=since_hours)
        alerts = store.wallet_alerts_since(since)
        if not alerts:
            console.print("[yellow]No alerts in the requested window[/yellow]")
            return
        table = Table(title=f"Wallet alerts (last {since_hours}h)")
        for col in ["time", "chain", "address", "action", "severity", "tx"]:
            table.add_column(col)
        for alert in alerts:
            table.add_row(
                alert.block_time.isoformat(),
                alert.chain,
                alert.address[:12] + "...",
                alert.action,
                alert.severity,
                alert.tx_hash[:12] + "...",
            )
        console.print(table)
    finally:
        store.close()


@twitter_app.command("import")
def twitter_import(
    file: Annotated[Path, typer.Argument(help="CSV or JSONL file to import")],
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Import a single tweet export file."""
    store, cfg = _load(config)
    syncer = TwitterSync(store, cfg)
    syncer.load_accounts()
    count = syncer.import_file(file)
    syncer.enrich_recent_tweets()
    syncer.aggregate_signals()
    syncer.detect_and_store_alerts()
    console.print(f"[green]Imported {count} tweets from {file}[/green]")
    store.close()


accounts_app = typer.Typer(help="Manage twitter watchlist accounts")
twitter_app.add_typer(accounts_app, name="accounts")


@accounts_app.command("list")
def twitter_accounts_list(
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """List active twitter accounts."""
    store, _cfg = _load(config)
    try:
        accounts = store.active_twitter_accounts()
        table = Table(title="Active twitter accounts")
        for col in ["handle", "type", "chains", "source", "confidence"]:
            table.add_column(col)
        for account in accounts:
            table.add_row(
                account.handle,
                account.entity_type,
                account.chains,
                account.source,
                f"{account.confidence:.2f}",
            )
        console.print(table)
    finally:
        store.close()


@accounts_app.command("add")
def twitter_accounts_add(
    handle: str = typer.Argument(..., help="Twitter handle without @"),
    config: str | None = typer.Option(None, "--config", "-c"),
    entity_type: str = typer.Option("trader", "--type", help="kol, trader, etc."),
    display_name: str = typer.Option("", "--name", help="Display name"),
) -> None:
    """Add an account to the watchlist."""
    store, cfg = _load(config)
    syncer = TwitterSync(store, cfg)
    count = syncer.add_account(
        handle, entity_type=entity_type, display_name=display_name
    )
    console.print(f"[green]Added/updated {count} account(s)[/green]")
    store.close()


@accounts_app.command("promote")
def twitter_accounts_promote(
    handle: str = typer.Argument(..., help="Discovered handle to promote"),
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Promote a discovered account to active watchlist."""
    store, cfg = _load(config)
    syncer = TwitterSync(store, cfg)
    if syncer.promote_account(handle):
        console.print(f"[green]Promoted {handle} to active[/green]")
    else:
        console.print(f"[yellow]Handle not found: {handle}[/yellow]")
    store.close()


@twitter_app.command("review")
def twitter_review(
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """List discovered handles pending review."""
    store, _cfg = _load(config)
    try:
        accounts = store.discovered_twitter_accounts()
        if not accounts:
            console.print("[yellow]No discovered accounts pending review[/yellow]")
            return
        table = Table(title="Discovered twitter accounts (inactive)")
        for col in ["handle", "type", "source"]:
            table.add_column(col)
        for account in accounts:
            table.add_row(account.handle, account.entity_type, account.source)
        console.print(table)
        console.print(
            "[dim]Promote with: ccquant twitter accounts promote HANDLE[/dim]"
        )
    finally:
        store.close()


@twitter_app.command("alerts")
def twitter_alerts(
    config: str | None = typer.Option(None, "--config", "-c"),
    since_hours: int = typer.Option(24, "--since", help="Hours to look back"),
) -> None:
    """Show recent tweet alerts from imported data."""
    from datetime import UTC, datetime, timedelta

    store, _cfg = _load(config)
    try:
        since = datetime.now(tz=UTC) - timedelta(hours=since_hours)
        alerts = store.tweet_alerts_since(since)
        if not alerts:
            console.print("[yellow]No tweet alerts in the requested window[/yellow]")
            return
        table = Table(title=f"Tweet alerts (last {since_hours}h)")
        for col in ["time", "handle", "type", "severity", "symbols"]:
            table.add_column(col)
        for alert in alerts:
            table.add_row(
                alert.posted_at.isoformat(),
                alert.handle,
                alert.alert_type,
                alert.severity,
                alert.symbols,
            )
        console.print(table)
    finally:
        store.close()


@research_app.command("run")
def research_run(
    strategy: Annotated[
        str,
        typer.Option(
            "--strategy",
            "-s",
            help="Strategy name (config/strategies/{name}.yaml) or path to YAML",
        ),
    ] = "cs_mom_oi_regime",
    config: str | None = typer.Option(None, "--config", "-c"),
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Directory for JSON report (default: data/research)",
        ),
    ] = None,
    no_write: bool = typer.Option(
        False,
        "--no-write",
        help="Skip writing the JSON report artifact",
    ),
) -> None:
    """Run a strategy research template (walk-forward, costs, scale gates)."""
    from ccquant.strategy import run_strategy_detailed
    from ccquant.strategy.spec import default_strategy_config_path, load_strategy_config

    cfg = load_config(config)
    strategy_path = Path(strategy)
    if strategy_path.is_file():
        strat_cfg_path = strategy_path
    else:
        strat_cfg_path = default_strategy_config_path(strategy)
    if not strat_cfg_path.is_file():
        raise typer.BadParameter(f"strategy config not found: {strat_cfg_path}")

    write_dir = None if no_write else (out or Path("data/research"))
    strat = load_strategy_config(strat_cfg_path)
    result = run_strategy_detailed(
        database=cfg.database,
        config_path=strat_cfg_path,
        config=strat,
        write_dir=write_dir,
    )
    report = result.report
    console.print(f"[bold]{report.strategy_name}[/bold]  hash={report.config_hash}")
    console.print(
        f"panel={strat.panel}  "
        f"history={report.data_min_date} -> {report.data_max_date}  "
        f"({report.n_calendar_days} calendar days)  "
        f"symbols={report.n_symbols}  folds={report.n_folds}"
    )
    if report.n_calendar_days < 365:
        console.print(
            "[yellow]WARNING: panel history < 365 days — not a multi-year test. "
            "Run: uv run ccquant sync backfill --interval 1d --full --force "
            "--top 50[/yellow]"
        )
    metrics = report.oos_metrics
    table = Table(title="OOS metrics")
    table.add_column("metric")
    table.add_column("value")
    for key in (
        "net_sharpe",
        "gross_sharpe",
        "ir_ew",
        "sortino",
        "max_drawdown",
        "calmar",
        "hit_rate",
        "avg_turnover",
        "n_days",
    ):
        val = metrics.get(key, float("nan"))
        table.add_row(key, f"{val:.4f}" if isinstance(val, float) else str(val))
    console.print(table)
    console.print(
        f"capacity_usd={report.capacity_usd:,.0f}  "
        f"target_notional={strat.target_notional_usd:,.0f}"
    )
    if report.passed:
        console.print("[green]PASSED scale gates[/green]")
    else:
        console.print("[red]FAILED scale gates[/red]")
        for reason in report.gate_reasons:
            console.print(f"  - {reason}")
    if write_dir is not None:
        report_path = write_dir / f"{report.strategy_name}_{report.config_hash}.json"
        console.print(f"[dim]report -> {report_path}[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
