"""Forward-return labels and excess returns vs benchmarks."""

from __future__ import annotations

import polars as pl

from ccquant.strategy.spec import LabelSpec, StrategyConfig


def add_forward_returns(df: pl.DataFrame, horizons: tuple[int, ...]) -> pl.DataFrame:
    """Close-to-close forward returns at each horizon (known only after t+h)."""
    out = df.sort(["symbol", "date"])
    for h in horizons:
        out = out.with_columns(
            (pl.col("close").shift(-h).over("symbol") / pl.col("close") - 1.0).alias(
                f"fwd_ret_{h}d"
            )
        )
    return out


def add_excess_returns(df: pl.DataFrame, horizons: tuple[int, ...]) -> pl.DataFrame:
    """Excess forward returns vs equal-weight universe and vs BTC."""
    out = df
    for h in horizons:
        col = f"fwd_ret_{h}d"
        if col not in out.columns:
            continue
        ew = pl.col(col).mean().over("date").alias(f"ew_fwd_ret_{h}d")
        out = out.with_columns(ew)
        out = out.with_columns(
            (pl.col(col) - pl.col(f"ew_fwd_ret_{h}d")).alias(f"excess_ew_{h}d")
        )
        if "symbol" in out.columns:
            btc = (
                out.filter(pl.col("symbol") == "BTC")
                .select(["date", pl.col(col).alias(f"btc_fwd_ret_{h}d")])
                .unique(subset=["date"])
            )
            out = out.join(btc, on="date", how="left").with_columns(
                (pl.col(col) - pl.col(f"btc_fwd_ret_{h}d")).alias(f"excess_btc_{h}d")
            )
    return out


def build_labels(df: pl.DataFrame, config: StrategyConfig | LabelSpec) -> pl.DataFrame:
    """Attach forward and excess-return label columns."""
    spec = config.label if isinstance(config, StrategyConfig) else config
    out = add_forward_returns(df, spec.horizons)
    out = add_excess_returns(out, spec.horizons)
    return out
