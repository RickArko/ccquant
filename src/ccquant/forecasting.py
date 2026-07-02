from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl


def load_daily_panel(database: str | Path) -> pl.DataFrame:
    """Return daily OHLCV rows sorted for forecasting pipelines."""
    with duckdb.connect(str(database), read_only=True) as conn:
        return conn.execute(
            """
            select symbol, date, open, high, low, close, volume, source
            from ohlcv_daily
            order by symbol, date, source
            """
        ).pl()


def load_hourly_panel(database: str | Path) -> pl.DataFrame:
    """Return hourly OHLCV rows sorted for forecasting pipelines."""
    with duckdb.connect(str(database), read_only=True) as conn:
        return conn.execute(
            """
            select symbol, hour, open, high, low, close, volume, source
            from ohlcv_hourly
            order by symbol, hour, source
            """
        ).pl()

