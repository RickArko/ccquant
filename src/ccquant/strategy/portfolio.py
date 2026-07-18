"""Cross-sectional portfolio construction (dollar-neutral L/S)."""

from __future__ import annotations

import math

import polars as pl

from ccquant.strategy.spec import StrategyConfig, UniverseSpec


def _rebalance_mask(dates: pl.Series, how: str) -> pl.Series:
    """True on rebalance days. ``weekly`` ≈ Friday (weekday==4) or last available."""
    if how != "weekly":
        return pl.Series("is_rebalance", [True] * len(dates))
    # Build from unique sorted dates so "last available before weekend" works.
    uniq = dates.unique().sort()
    # Polars Date weekday: Monday=1 ... Sunday=7 in some versions; use dt.weekday().
    df = pl.DataFrame({"date": uniq}).with_columns(
        pl.col("date").dt.weekday().alias("wd")
    )
    # Prefer Friday (5 in ISO); else mark last date of each ISO week.
    df = df.with_columns(
        pl.col("date").dt.strftime("%G-%V").alias("iso_week"),
    )
    friday = df.filter(pl.col("wd") == 5).select("date")
    if friday.height == 0:
        # Fallback: last day of each ISO week present in the panel.
        week_ends = df.group_by("iso_week").agg(pl.col("date").max().alias("date"))
        rebal_dates = set(week_ends["date"].to_list())
    else:
        rebal_dates = set(friday["date"].to_list())
    return dates.is_in(sorted(rebal_dates))


def limit_universe(df: pl.DataFrame, universe: UniverseSpec) -> pl.DataFrame:
    """Keep top_n symbols by average ADV (liquidity proxy)."""
    if universe.top_n <= 0 or "adv_usd" not in df.columns:
        return df
    ranking = (
        df.group_by("symbol")
        .agg(pl.col("adv_usd").mean().alias("mean_adv"))
        .sort("mean_adv", descending=True)
        .head(universe.top_n)
        .select("symbol")
    )
    return df.join(ranking, on="symbol", how="inner")


def build_target_weights(df: pl.DataFrame, config: StrategyConfig) -> pl.DataFrame:
    """Assign dollar-neutral target weights on rebalance days; forward-fill daily.

    Long top quantile of ``alpha_score``, short bottom quantile. Flat when
    ``regime_active`` is False. Gross exposure scaled toward ``vol_target_ann``.
    """
    port = config.portfolio
    out = limit_universe(df, config.universe).sort(["date", "symbol"])
    dates = out["date"]
    out = out.with_columns(_rebalance_mask(dates, port.rebalance).alias("is_rebalance"))

    # Rank within date among liquid names.
    eligible = out.with_columns(
        (pl.col("adv_usd").fill_null(0.0) >= port.min_adv_usd).alias("adv_ok"),
        (
            pl.col("alpha_score").is_not_null()
            & pl.col("alpha_score").is_not_nan()
        ).alias("score_ok"),
    )
    if "regime_active" not in eligible.columns:
        eligible = eligible.with_columns(pl.lit(True).alias("regime_active"))

    # Quantile rank in [0, 1] among eligible names on rebalance days.
    eligible = eligible.with_columns(
        pl.when(pl.col("adv_ok") & pl.col("score_ok") & pl.col("is_rebalance"))
        .then(
            pl.col("alpha_score")
            .rank(method="average")
            .over("date")
            / pl.col("alpha_score").count().over("date")
        )
        .otherwise(None)
        .alias("cs_rank")
    )

    n_q = max(port.n_quantiles, 2)
    long_cut = 1.0 - 1.0 / n_q
    short_cut = 1.0 / n_q

    eligible = eligible.with_columns(
        pl.when(~pl.col("regime_active").fill_null(True))
        .then(0.0)
        .when(pl.col("cs_rank") >= long_cut)
        .then(1.0)
        .when(pl.col("cs_rank") <= short_cut)
        .then(-1.0)
        .otherwise(0.0)
        .alias("sleeve")
    )

    # Equal-weight within long/short sleeves → dollar-neutral if both sides nonempty.
    counts = eligible.group_by("date").agg(
        pl.col("sleeve").eq(1.0).sum().alias("n_long"),
        pl.col("sleeve").eq(-1.0).sum().alias("n_short"),
    )
    eligible = eligible.join(counts, on="date", how="left").with_columns(
        pl.when(pl.col("sleeve") == 1.0)
        .then(0.5 / pl.col("n_long").clip(lower_bound=1))
        .when(pl.col("sleeve") == -1.0)
        .then(-0.5 / pl.col("n_short").clip(lower_bound=1))
        .otherwise(0.0)
        .alias("w_raw")
    )

    # Vol-target: scale gross exposure using trailing strategy-ish vol proxy
    # (median of member vols). On non-rebalance days w_raw is 0; we forward-fill.
    vol_col = f"vol_{config.features.vol_window}d"
    if vol_col in eligible.columns:
        day_vol = (
            eligible.filter(pl.col("sleeve") != 0.0)
            .group_by("date")
            .agg(pl.col(vol_col).median().alias("book_vol"))
        )
        eligible = eligible.join(day_vol, on="date", how="left")
        # Daily vol target ≈ ann / sqrt(365)
        daily_target = port.vol_target_ann / math.sqrt(365.0)
        eligible = eligible.with_columns(
            pl.when(pl.col("book_vol").is_not_null() & (pl.col("book_vol") > 1e-12))
            .then((daily_target / pl.col("book_vol")).clip(0.1, 5.0))
            .otherwise(1.0)
            .alias("vol_scale")
        )
    else:
        eligible = eligible.with_columns(pl.lit(1.0).alias("vol_scale"))

    eligible = eligible.with_columns(
        (pl.col("w_raw") * pl.col("vol_scale")).alias("w_target")
    )

    # Forward-fill weights per symbol from last rebalance; zero before first.
    eligible = eligible.sort(["symbol", "date"]).with_columns(
        pl.when(pl.col("is_rebalance"))
        .then(pl.col("w_target"))
        .otherwise(None)
        .forward_fill()
        .over("symbol")
        .fill_null(0.0)
        .alias("weight")
    )
    # Flatten when regime turns off between rebalances.
    eligible = eligible.with_columns(
        pl.when(pl.col("regime_active").fill_null(True))
        .then(pl.col("weight"))
        .otherwise(0.0)
        .alias("weight")
    )
    return eligible


def dollar_neutral_check(weights: pl.Series, tol: float = 1e-6) -> bool:
    """Return True if sum of weights is approximately zero."""
    total = float(weights.sum())
    return abs(total) <= tol


def gross_exposure(weights: pl.Series) -> float:
    return float(weights.abs().sum())
