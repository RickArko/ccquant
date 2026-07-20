"""Transaction cost model: fees + ADV-participation slippage."""

from __future__ import annotations

import math

import polars as pl

from ccquant.strategy.spec import CostModel, StrategyConfig


def apply_costs(
    df: pl.DataFrame,
    config: StrategyConfig | CostModel,
    *,
    weight_col: str = "weight",
    notional: float | None = None,
) -> pl.DataFrame:
    """Attach turnover, cost drag, and net portfolio returns.

    Gross daily return ≈ Σ w_{t-1} * r_t.
    Cost on day t ≈ (fee_bps_rt/1e4) * one_way_turnover
      + slippage_coef * Σ |Δw| * sqrt(|Δw| * notional / ADV).
    """
    cost = config.cost if isinstance(config, StrategyConfig) else config
    target_notional = (
        notional
        if notional is not None
        else (
            config.target_notional_usd
            if isinstance(config, StrategyConfig)
            else 1_000_000.0
        )
    )
    out = df.sort(["symbol", "date"]).with_columns(
        pl.col(weight_col).shift(1).over("symbol").fill_null(0.0).alias("w_lag"),
        pl.col("ret_1d").fill_null(0.0).alias("ret_1d_filled"),
    )
    out = out.with_columns(
        (pl.col(weight_col) - pl.col("w_lag")).alias("dw"),
        (pl.col("w_lag") * pl.col("ret_1d_filled")).alias("w_ret"),
    )

    fee_rate = cost.fee_bps_rt / 10_000.0
    # Per-name slippage proxy; ADV in USD.
    # Never fall back to ADV=1 (explodes participation/slippage). Missing or
    # sub-threshold ADV → fee-only for that name (zero slippage).
    min_adv_for_slip = 1_000.0
    if "adv_usd" in out.columns:
        adv = pl.col("adv_usd")
    else:
        adv = pl.lit(None).cast(pl.Float64)
    adv_ok = adv.is_not_null() & (adv >= min_adv_for_slip)
    participation = pl.when(adv_ok).then(
        (pl.col("dw").abs() * target_notional) / adv
    ).otherwise(0.0)
    slip = pl.when(adv_ok).then(
        cost.slippage_coef * pl.col("dw").abs() * participation.sqrt()
    ).otherwise(0.0)

    daily = (
        out.group_by("date")
        .agg(
            pl.col("w_ret").sum().alias("gross_ret"),
            pl.col("dw").abs().sum().alias("turnover"),
            slip.sum().alias("slippage"),
            pl.col(weight_col).sum().alias("net_exposure"),
            pl.col(weight_col).abs().sum().alias("gross_exposure"),
            adv.median().alias("median_adv"),
            participation.max().alias("max_participation"),
        )
        .sort("date")
        .with_columns(
            (pl.col("turnover") * fee_rate).alias("fee_cost"),
        )
        .with_columns(
            (pl.col("fee_cost") + pl.col("slippage").fill_null(0.0)).alias(
                "total_cost"
            ),
        )
        .with_columns(
            (pl.col("gross_ret") - pl.col("total_cost")).alias("net_ret"),
        )
    )
    return daily


def estimate_capacity_usd(
    df: pl.DataFrame,
    *,
    max_participation: float,
    weight_col: str = "weight",
) -> float:
    """Max notional such that max |w| * notional / ADV ≤ max_participation."""
    if max_participation <= 0:
        return 0.0
    panel = df.filter(pl.col(weight_col).abs() > 0)
    if panel.height == 0 or "adv_usd" not in panel.columns:
        return 0.0
    # notional <= max_participation * ADV / |w|
    ratios = panel.select(
        (
            pl.col("adv_usd").fill_null(0.0)
            * max_participation
            / pl.col(weight_col).abs().clip(lower_bound=1e-12)
        ).alias("cap")
    ).filter(pl.col("cap") > 0)
    if ratios.height == 0:
        return 0.0
    # Conservative: 5th percentile across position-days.
    q = ratios["cap"].quantile(0.05)
    return float(q) if q is not None else 0.0


def cost_drag_from_turnover(turnover: float, fee_bps_rt: float) -> float:
    """Simple fee-only drag helper for unit tests."""
    return turnover * (fee_bps_rt / 10_000.0)


def sqrt_participation_slippage(
    abs_dw: float, notional: float, adv: float, coef: float
) -> float:
    if adv <= 0 or abs_dw <= 0:
        return 0.0
    return coef * abs_dw * math.sqrt(abs_dw * notional / adv)
