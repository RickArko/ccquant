"""Point-in-time feature builders for strategy research panels."""

from __future__ import annotations

import polars as pl

from ccquant.strategy.spec import FeatureSpec, RegimeSpec, StrategyConfig


def lag_columns(
    df: pl.DataFrame,
    columns: list[str],
    lag: int,
    *,
    by: str | None = None,
) -> pl.DataFrame:
    """Lag columns by ``lag`` rows (within ``by`` groups when provided).

    Macro / on-chain series should be lagged ≥1 day unless same-day availability
    is proven. Date-global series may omit ``by`` so the lag is calendar-aligned
    after sorting by date.
    """
    if lag <= 0:
        return df
    present = [c for c in columns if c in df.columns]
    if not present:
        return df
    out = df
    for col in present:
        expr = pl.col(col).shift(lag).alias(f"{col}_lag{lag}")
        if by is not None:
            expr = pl.col(col).shift(lag).over(by).alias(f"{col}_lag{lag}")
        out = out.with_columns(expr)
    return out


def _cs_zscore(col: str) -> pl.Expr:
    """Cross-sectional z-score; zero when cross-section std is 0/null (avoid NaN)."""
    std = pl.col(col).std().over("date")
    return (
        pl.when(std.is_null() | (std < 1e-12))
        .then(0.0)
        .otherwise((pl.col(col) - pl.col(col).mean().over("date")) / std)
        .fill_nan(0.0)
        .alias(f"{col}_cs_z")
    )


def add_price_features(df: pl.DataFrame, spec: FeatureSpec) -> pl.DataFrame:
    """Add momentum, vol, volume z-score, and OI change features (per symbol)."""
    out = df.sort(["symbol", "date"]).with_columns(
        pl.col("close").pct_change().over("symbol").alias("ret_1d"),
    )
    for w in spec.mom_windows:
        out = out.with_columns(
            (pl.col("close") / pl.col("close").shift(w).over("symbol") - 1.0).alias(
                f"mom_{w}d"
            )
        )
    out = out.with_columns(
        pl.col("ret_1d")
        .rolling_std(spec.vol_window)
        .over("symbol")
        .alias(f"vol_{spec.vol_window}d"),
        (
            (
                pl.col("volume")
                - pl.col("volume")
                .rolling_mean(spec.volume_z_window)
                .over("symbol")
            )
            / pl.col("volume").rolling_std(spec.volume_z_window).over("symbol")
        ).alias("volume_z"),
        (pl.col("close") * pl.col("volume"))
        .rolling_mean(spec.vol_window)
        .over("symbol")
        .alias("adv_usd"),
    )
    if "total_open_interest_usd" in out.columns:
        out = out.with_columns(
            (
                pl.col("total_open_interest_usd")
                / pl.col("total_open_interest_usd")
                .shift(spec.oi_change_window)
                .over("symbol")
                - 1.0
            ).alias("oi_chg")
        )
    else:
        out = out.with_columns(pl.lit(None).cast(pl.Float64).alias("oi_chg"))
    return out


def add_macro_regime(df: pl.DataFrame, regime: RegimeSpec) -> pl.DataFrame:
    """Build a lagged date-global liquidity regime score.

    Composite ≈ z(M2 growth) + z(WALCL growth) − z(real-rate Δ).
    Values are identical across symbols for a given date (mart caveat).
    """
    lag = max(1, regime.lag_days)
    # Collapse to date-level macro so lag is calendar-clean, then join back.
    macro_cols = [c for c in ("m2sl", "walcl", "dgs10", "t10yie") if c in df.columns]
    if not macro_cols:
        return df.with_columns(
            pl.lit(0.0).alias("regime_score"),
            pl.lit(True).alias("regime_active"),
        )

    by_date = (
        df.select(["date", *macro_cols])
        .unique(subset=["date"])
        .sort("date")
        .with_columns(
            [
                pl.col(c).shift(lag).alias(f"{c}_pit")
                for c in macro_cols
            ]
        )
    )
    # Growth / deltas on PIT-lagged levels.
    exprs: list[pl.Expr] = []
    if "m2sl_pit" in by_date.columns:
        exprs.append(pl.col("m2sl_pit").pct_change().alias("m2_g"))
    if "walcl_pit" in by_date.columns:
        exprs.append(pl.col("walcl_pit").pct_change().alias("walcl_g"))
    if "dgs10_pit" in by_date.columns and "t10yie_pit" in by_date.columns:
        exprs.append(
            (pl.col("dgs10_pit") - pl.col("t10yie_pit")).diff().alias("real_rate_d")
        )
    elif "dgs10_pit" in by_date.columns:
        exprs.append(pl.col("dgs10_pit").diff().alias("real_rate_d"))
    by_date = by_date.with_columns(exprs)

    z_window = max(regime.z_window, 20)

    def _z(col: str) -> pl.Expr:
        return (
            (pl.col(col) - pl.col(col).rolling_mean(z_window))
            / pl.col(col).rolling_std(z_window)
        ).alias(f"{col}_z")

    z_parts: list[pl.Expr] = []
    if "m2_g" in by_date.columns:
        z_parts.append(_z("m2_g"))
    if "walcl_g" in by_date.columns:
        z_parts.append(_z("walcl_g"))
    if "real_rate_d" in by_date.columns:
        z_parts.append(_z("real_rate_d"))
    by_date = by_date.with_columns(z_parts)
    score_expr = pl.lit(0.0)
    if "m2_g_z" in by_date.columns:
        score_expr = score_expr + pl.col("m2_g_z").fill_null(0.0)
    if "walcl_g_z" in by_date.columns:
        score_expr = score_expr + pl.col("walcl_g_z").fill_null(0.0)
    if "real_rate_d_z" in by_date.columns:
        score_expr = score_expr - pl.col("real_rate_d_z").fill_null(0.0)
    by_date = by_date.with_columns(
        score_expr.alias("regime_score"),
        (score_expr >= regime.risk_off_z).alias("regime_active"),
    ).select(["date", "regime_score", "regime_active"])

    return df.join(by_date, on="date", how="left")


def build_alpha_score(df: pl.DataFrame, spec: FeatureSpec) -> pl.DataFrame:
    """Cross-sectional composite score from momentum / volume / OI features."""
    parts: list[str] = []
    out = df
    for w in spec.mom_windows:
        col = f"mom_{w}d"
        if col in out.columns:
            out = out.with_columns(_cs_zscore(col))
            parts.append(f"{col}_cs_z")
    if "volume_z" in out.columns:
        out = out.with_columns(_cs_zscore("volume_z"))
        parts.append("volume_z_cs_z")
    if "oi_chg" in out.columns:
        out = out.with_columns(_cs_zscore("oi_chg"))
        parts.append("oi_chg_cs_z")
    if not parts:
        return out.with_columns(pl.lit(0.0).alias("alpha_score"))
    score: pl.Expr = pl.lit(0.0)
    for c in parts:
        score = score + pl.col(c).fill_null(0.0).fill_nan(0.0)
    # Prefer higher mom, mild volume confirmation; subtract vol (risk).
    if f"vol_{spec.vol_window}d" in out.columns:
        out = out.with_columns(_cs_zscore(f"vol_{spec.vol_window}d"))
        score = score - pl.col(f"vol_{spec.vol_window}d_cs_z").fill_null(0.0).fill_nan(
            0.0
        )
    return out.with_columns(score.alias("alpha_score"))


def build_features(df: pl.DataFrame, config: StrategyConfig) -> pl.DataFrame:
    """Full PIT feature pipeline for the strategy template."""
    required = {"symbol", "date", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"panel missing required columns: {sorted(missing)}")
    out = add_price_features(df, config.features)
    out = add_macro_regime(out, config.regime)
    out = build_alpha_score(out, config.features)
    return out
