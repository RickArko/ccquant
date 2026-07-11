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
db_app = typer.Typer(help="Database backup and maintenance")
wallet_app = typer.Typer(help="Wallet intelligence and on-chain tracking")
twitter_app = typer.Typer(help="Twitter / X tweet tracking (import-only)")
app.add_typer(sync_app, name="sync")
app.add_typer(export_app, name="export")
app.add_typer(migrate_app, name="migrate")
app.add_typer(db_app, name="db")
app.add_typer(wallet_app, name="wallet")
app.add_typer(twitter_app, name="twitter")
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
    top: int | None = typer.Option(None, "--top", help="Limit to top-N ranked assets"),
) -> None:
    """Backfill or tail-refresh OHLCV data."""
    if interval not in {"1d", "1h"}:
        raise typer.BadParameter("interval must be 1d or 1h")
    store, cfg = _load(config)
    syncer = MarketSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.backfill(interval=interval, full=full, top=top)
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


@sync_app.command("wallets")
def sync_wallets(
    config: str | None = typer.Option(None, "--config", "-c"),
    full: bool = typer.Option(False, "--full/--no-full"),
    no_tail: bool = typer.Option(False, "--no-tail", help="Skip RPC tail refresh"),
) -> None:
    """Sync wallet registry, historical extracts, and tail activity."""
    store, cfg = _load(config)
    syncer = WalletSync(store, cfg)

    async def run() -> dict[str, int]:
        try:
            return await syncer.sync_all(full=full, tail=not no_tail)
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
        True, "--dbt/--no-dbt", help="Run dbt build + test after Python sync"
    ),
    wallets: bool = typer.Option(
        True, "--wallets/--no-wallets", help="Run wallet intelligence sync"
    ),
    tweets: bool = typer.Option(
        True, "--tweets/--no-tweets", help="Run tweet import sync"
    ),
) -> None:
    """Sync everything: universe + daily + hourly + OI + macro + wallets + tweets + dbt.

    One command to bring the local DB up to today. Runs:
    1. Universe refresh (top-cap rankings + exchange pair probes)
    2. Daily tail-refresh (full backfill already complete)
    3. Hourly tail-refresh (top-N assets)
    4. Open interest tail-refresh (Binance + Bybit + OKX, per config toggles)
    5. Macro sync (FRED series, if FRED_API_KEY is set)
    6. Wallet intelligence sync (registry + history + tail)
    7. Tweet import sync (inbox CSV/JSONL)
    8. dbt build + test (transforms raw -> staging/marts/signals/events)
    9. Status table
    """
    store, cfg = _load(config)
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

            # 5. Macro sync (FRED)
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

    # 8. dbt build + test
    if dbt:
        console.print("\n[dim]dbt build...[/dim]")
        dbt_ok = _run_dbt("build")
        if dbt_ok:
            console.print("[green]dbt build: PASS[/green]")
        else:
            console.print("[yellow]dbt build: skipped or failed[/yellow]")

    # 9. Status
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
            "macro_series",
            "macro_sync_state",
            "wallet_registry",
            "wallet_transfers",
            "wallet_positions_daily",
            "wallet_sync_state",
            "wallet_signals_daily",
            "wallet_alerts",
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
    chain: str = typer.Option("solana", "--chain", help="solana, arbitrum, ethereum"),
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
                partition_date=parsed_date,
                parquet_path=parquet,
            )
        finally:
            await syncer.close()
            store.close()

    count = asyncio.run(run())
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
