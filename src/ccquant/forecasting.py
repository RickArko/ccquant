from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl


def _table_exists(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = conn.execute(
        "select count(*) from information_schema.tables"
        " where table_schema = ? and table_name = ?",
        [schema, table],
    ).fetchone()
    return bool(row and row[0] > 0)


def load_daily_panel(database: str | Path) -> pl.DataFrame:
    """Return daily OHLCV rows sorted for forecasting pipelines.

    Reads from the dbt marts layer (fct_ohlcv_daily) when available,
    falling back to raw ohlcv_daily for backward compatibility.
    """
    with duckdb.connect(str(database), read_only=True) as conn:
        if _table_exists(conn, "main_marts", "fct_ohlcv_daily"):
            df = pl.from_arrow(
                conn.execute(
                    """
                    select symbol, date, open, high, low, close, volume, source
                    from main_marts.fct_ohlcv_daily
                    order by symbol, date
                    """
                ).to_arrow_table()
            )
        else:
            df = pl.from_arrow(
                conn.execute(
                    """
                    select symbol, date, open, high, low, close, volume, source
                    from ohlcv_daily
                    order by symbol, date, source
                    """
                ).to_arrow_table()
            )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()


def load_hourly_panel(database: str | Path) -> pl.DataFrame:
    """Return hourly OHLCV rows sorted for forecasting pipelines."""
    with duckdb.connect(str(database), read_only=True) as conn:
        df = pl.from_arrow(
            conn.execute(
                """
                select symbol, hour, open, high, low, close, volume, source
                from ohlcv_hourly
                order by symbol, hour, source
                """
            ).to_arrow_table()
        )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()


def load_signals_panel(database: str | Path) -> pl.DataFrame:
    """Return the canonical daily analytics panel from dbt marts.

    Joins price, open interest, on-chain signals, macro indicators,
    and event flags. Requires the dbt marts layer to be built.
    """
    with duckdb.connect(str(database), read_only=True) as conn:
        df = pl.from_arrow(
            conn.execute(
                """
                select * from main_marts.mart_signals_daily
                order by symbol, date
                """
            ).to_arrow_table()
        )
    return df if isinstance(df, pl.DataFrame) else df.to_frame()

