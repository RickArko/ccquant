from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ccquant.config import AppConfig, load_config
from ccquant.storage import MarketStore
from ccquant.sync import MarketSync

app = typer.Typer(help="Crypto OHLCV data and forecasting research toolkit")
sync_app = typer.Typer(help="Fetch and refresh market data")
export_app = typer.Typer(help="Export DuckDB tables")
migrate_app = typer.Typer(help="Migrate data between databases")
db_app = typer.Typer(help="Database backup and maintenance")
app.add_typer(sync_app, name="sync")
app.add_typer(export_app, name="export")
app.add_typer(migrate_app, name="migrate")
app.add_typer(db_app, name="db")
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
) -> None:
    """Sync everything: universe + daily + hourly + OI + macro + dbt + status.

    One command to bring the local DB up to today. Runs:
    1. Universe refresh (top-cap rankings + exchange pair probes)
    2. Daily tail-refresh (full backfill already complete)
    3. Hourly tail-refresh (top-N assets)
    4. Open interest tail-refresh (Binance + Bybit + OKX, per config toggles)
    5. Macro sync (FRED series, if FRED_API_KEY is set)
    6. dbt build + test (transforms raw -> staging/marts/signals/events)
    7. Status table
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

    # 6. dbt build + test
    if dbt:
        console.print("\n[dim]dbt build...[/dim]")
        dbt_ok = _run_dbt("build")
        if dbt_ok:
            console.print("[green]dbt build: PASS[/green]")
        else:
            console.print("[yellow]dbt build: skipped or failed[/yellow]")

    # 7. Status
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
