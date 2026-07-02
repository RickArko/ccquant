from __future__ import annotations

import asyncio
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
app.add_typer(sync_app, name="sync")
app.add_typer(export_app, name="export")
console = Console()


DEFAULT_EXPORT_DIR = Path("data/export")


def _load(config: str | None) -> tuple[MarketStore, AppConfig]:
    cfg = load_config(config)
    return MarketStore(cfg.database), cfg


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
        for table in ["assets", "ohlcv_daily", "ohlcv_hourly", "sync_state"]:
            path = store.export_table(table, out, fmt=fmt)
            console.print(f"[green]wrote[/green] {path}")
    finally:
        store.close()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
