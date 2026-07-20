"""Portfolio construction: cross-sectional L/S or directional macro timing."""

from __future__ import annotations

import math

import polars as pl

from ccquant.strategy.spec import StrategyConfig, UniverseSpec


def _rebalance_mask(dates: pl.Series, how: str) -> pl.Series:
    """True on rebalance days. ``weekly`` ≈ Friday or last available day of week."""
    if how != "weekly":
        return pl.Series("is_rebalance", [True] * len(dates))
    uniq = dates.unique().sort()
    df = pl.DataFrame({"date": uniq}).with_columns(
        pl.col("date").dt.weekday().alias("wd"),
        pl.col("date").dt.strftime("%G-%V").alias("iso_week"),
    )
    friday = df.filter(pl.col("wd") == 5).select("date")
    if friday.height == 0:
        week_ends = df.group_by("iso_week").agg(pl.col("date").max().alias("date"))
        rebal_dates = set(week_ends["date"].to_list())
    else:
        rebal_dates = set(friday["date"].to_list())
    return dates.is_in(sorted(rebal_dates))


def filter_symbols(df: pl.DataFrame, universe: UniverseSpec) -> pl.DataFrame:
    """Restrict to ``universe.symbols`` when the list is non-empty."""
    if not universe.symbols:
        return df
    wanted = {s.upper() for s in universe.symbols}
    return df.filter(pl.col("symbol").str.to_uppercase().is_in(sorted(wanted)))


def limit_universe(df: pl.DataFrame, universe: UniverseSpec) -> pl.DataFrame:
    """Keep configured symbols and/or top_n by average ADV."""
    out = filter_symbols(df, universe)
    if universe.symbols or universe.top_n <= 0 or "adv_usd" not in out.columns:
        return out
    ranking = (
        out.group_by("symbol")
        .agg(pl.col("adv_usd").mean().alias("mean_adv"))
        .sort("mean_adv", descending=True)
        .head(universe.top_n)
        .select("symbol")
    )
    return out.join(ranking, on="symbol", how="inner")


def _apply_vol_scale(
    eligible: pl.DataFrame,
    config: StrategyConfig,
    *,
    sleeve_col: str = "sleeve",
) -> pl.DataFrame:
    port = config.portfolio
    vol_col = f"vol_{config.features.vol_window}d"
    if vol_col in eligible.columns:
        day_vol = (
            eligible.filter(pl.col(sleeve_col) != 0.0)
            .group_by("date")
            .agg(pl.col(vol_col).median().alias("book_vol"))
        )
        eligible = eligible.join(day_vol, on="date", how="left")
        daily_target = port.vol_target_ann / math.sqrt(365.0)
        eligible = eligible.with_columns(
            pl.when(pl.col("book_vol").is_not_null() & (pl.col("book_vol") > 1e-12))
            .then((daily_target / pl.col("book_vol")).clip(0.1, 5.0))
            .otherwise(1.0)
            .alias("vol_scale")
        )
    else:
        eligible = eligible.with_columns(pl.lit(1.0).alias("vol_scale"))
    return eligible


def _forward_fill_weights(eligible: pl.DataFrame) -> pl.DataFrame:
    return eligible.sort(["symbol", "date"]).with_columns(
        pl.when(pl.col("is_rebalance"))
        .then(pl.col("w_target"))
        .otherwise(None)
        .forward_fill()
        .over("symbol")
        .fill_null(0.0)
        .alias("weight")
    )


def build_directional_weights(
    df: pl.DataFrame, config: StrategyConfig
) -> pl.DataFrame:
    """Single-name (or few-name) long/short/flat from macro ``regime_score`` bands.

    On rebalance days:
    - ``regime_score >= long_z`` → long (+1 before vol scale)
    - ``regime_score <= short_z`` → short (−1)
    - otherwise flat
    """
    port = config.portfolio
    regime = config.regime
    out = limit_universe(df, config.universe).sort(["date", "symbol"])
    out = out.with_columns(
        _rebalance_mask(out["date"], port.rebalance).alias("is_rebalance")
    )
    if "regime_score" not in out.columns:
        out = out.with_columns(pl.lit(0.0).alias("regime_score"))

    long_z = regime.long_z
    short_z = regime.short_z
    if short_z > long_z:
        short_z, long_z = long_z, short_z

    short_sleeve = -1.0 if port.allow_short else 0.0
    eligible = out.with_columns(
        pl.when(~pl.col("is_rebalance"))
        .then(None)
        .when(pl.col("regime_score") >= long_z)
        .then(1.0)
        .when(pl.col("regime_score") <= short_z)
        .then(short_sleeve)
        .otherwise(0.0)
        .alias("sleeve")
    )
    # Carry last sleeve for vol_scale join helper; w_raw only on rebalance.
    eligible = eligible.with_columns(
        pl.col("sleeve").forward_fill().over("symbol").fill_null(0.0).alias("sleeve_ff")
    )
    eligible = eligible.with_columns(
        pl.when(pl.col("is_rebalance"))
        .then(pl.col("sleeve").fill_null(0.0))
        .otherwise(0.0)
        .alias("w_raw")
    )
    eligible = _apply_vol_scale(eligible, config, sleeve_col="sleeve_ff")
    eligible = eligible.with_columns(
        (pl.col("w_raw") * pl.col("vol_scale")).alias("w_target")
    )
    return _forward_fill_weights(eligible)


def build_cross_section_weights(
    df: pl.DataFrame, config: StrategyConfig, *, long_only: bool = False
) -> pl.DataFrame:
    """CS portfolio from ``alpha_score`` quintiles (L/S or long-only)."""
    port = config.portfolio
    out = limit_universe(df, config.universe).sort(["date", "symbol"])
    out = out.with_columns(
        _rebalance_mask(out["date"], port.rebalance).alias("is_rebalance")
    )

    eligible = out.with_columns(
        (pl.col("adv_usd").fill_null(0.0) >= port.min_adv_usd).alias("adv_ok"),
        (
            pl.col("alpha_score").is_not_null()
            & pl.col("alpha_score").is_not_nan()
        ).alias("score_ok"),
    )
    if "regime_active" not in eligible.columns:
        eligible = eligible.with_columns(pl.lit(True).alias("regime_active"))

    eligible = eligible.with_columns(
        pl.when(pl.col("adv_ok") & pl.col("score_ok") & pl.col("is_rebalance"))
        .then(
            pl.col("alpha_score").rank(method="average").over("date")
            / pl.col("alpha_score").count().over("date")
        )
        .otherwise(None)
        .alias("cs_rank")
    )

    n_q = max(port.n_quantiles, 2)
    long_cut = 1.0 - 1.0 / n_q
    short_cut = 1.0 / n_q

    if long_only:
        eligible = eligible.with_columns(
            pl.when(~pl.col("regime_active").fill_null(True))
            .then(0.0)
            .when(pl.col("cs_rank") >= long_cut)
            .then(1.0)
            .otherwise(0.0)
            .alias("sleeve")
        )
        counts = eligible.group_by("date").agg(
            pl.col("sleeve").eq(1.0).sum().alias("n_long"),
        )
        eligible = eligible.join(counts, on="date", how="left").with_columns(
            pl.when(pl.col("sleeve") == 1.0)
            .then(1.0 / pl.col("n_long").clip(lower_bound=1))
            .otherwise(0.0)
            .alias("w_raw")
        )
    else:
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

    eligible = _apply_vol_scale(eligible, config)
    eligible = eligible.with_columns(
        (pl.col("w_raw") * pl.col("vol_scale")).alias("w_target")
    )
    eligible = _forward_fill_weights(eligible)
    return eligible.with_columns(
        pl.when(pl.col("regime_active").fill_null(True))
        .then(pl.col("weight"))
        .otherwise(0.0)
        .alias("weight")
    )


def build_ts_mom_weights(df: pl.DataFrame, config: StrategyConfig) -> pl.DataFrame:
    """BTC (or single-name) dual-momentum directional weights from regime_score."""
    # Reuse directional bands: ts_mom features set regime_score ∈ {-1,0,+1}.
    cfg = config
    if config.regime.long_z != 0.5 or config.regime.short_z != -0.5:
        from dataclasses import replace

        cfg = replace(
            config,
            regime=replace(config.regime, long_z=0.5, short_z=-0.5),
        )
    return build_directional_weights(df, cfg)


def build_target_weights(df: pl.DataFrame, config: StrategyConfig) -> pl.DataFrame:
    """Dispatch to CS / long-only / directional / ts_mom construction."""
    mode = config.portfolio.mode
    if mode == "directional":
        return build_directional_weights(df, config)
    if mode == "ts_mom":
        return build_ts_mom_weights(df, config)
    if mode == "long_only":
        return build_cross_section_weights(df, config, long_only=True)
    return build_cross_section_weights(df, config, long_only=False)


def dollar_neutral_check(weights: pl.Series, tol: float = 1e-6) -> bool:
    """Return True if sum of weights is approximately zero."""
    total = float(weights.sum())
    return abs(total) <= tol


def gross_exposure(weights: pl.Series) -> float:
    return float(weights.abs().sum())
