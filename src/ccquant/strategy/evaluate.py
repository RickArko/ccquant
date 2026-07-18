"""Purged walk-forward evaluation and risk metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import polars as pl

from ccquant.strategy.costs import apply_costs, estimate_capacity_usd
from ccquant.strategy.features import build_features
from ccquant.strategy.labels import build_labels
from ccquant.strategy.portfolio import build_target_weights
from ccquant.strategy.spec import StrategyConfig, WalkForwardSpec


def _f(value: Any) -> float:
    if value is None:
        return float("nan")
    return float(value)


@dataclass(frozen=True)
class FoldWindow:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def purged_folds(
    dates: list[date],
    spec: WalkForwardSpec,
) -> list[FoldWindow]:
    """Expanding/rolling train → embargo → test folds.

    Embargo gap between train_end and test_start is ≥ ``embargo_days`` calendar
    days from the sorted unique trading dates index distance.
    """
    if not dates:
        return []
    uniq = sorted(set(dates))
    n = len(uniq)
    folds: list[FoldWindow] = []
    train = spec.train_days
    test = spec.test_days
    step = max(spec.step_days, 1)
    embargo = max(spec.embargo_days, 0)
    i = train
    while i + embargo + test <= n:
        train_start = uniq[0]  # expanding window from start
        train_end = uniq[i - 1]
        test_start = uniq[i + embargo]
        test_end = uniq[i + embargo + test - 1]
        folds.append(
            FoldWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        i += step
    return folds


def _ann_factor() -> float:
    return math.sqrt(365.0)


def sharpe_ratio(returns: pl.Series) -> float:
    r = returns.drop_nulls()
    if r.len() < 2:
        return float("nan")
    mu = _f(r.mean())
    sigma = _f(r.std())
    if sigma < 1e-15:
        return float("nan")
    return (mu / sigma) * _ann_factor()


def sortino_ratio(returns: pl.Series) -> float:
    r = returns.drop_nulls()
    if r.len() < 2:
        return float("nan")
    mu = _f(r.mean())
    downside = r.filter(r < 0.0)
    if downside.len() < 1:
        return float("nan")
    dd = _f(downside.std())
    if dd < 1e-15:
        return float("nan")
    return (mu / dd) * _ann_factor()


def max_drawdown(returns: pl.Series) -> float:
    r = returns.drop_nulls()
    if r.len() == 0:
        return float("nan")
    equity = (1.0 + r).cum_prod()
    peak = equity.cum_max()
    dd = (equity - peak) / peak
    return _f(dd.min())


def calmar_ratio(returns: pl.Series) -> float:
    r = returns.drop_nulls()
    if r.len() < 2:
        return float("nan")
    ann_ret = _f(r.mean()) * 365.0
    mdd = abs(max_drawdown(r))
    if mdd < 1e-15:
        return float("nan")
    return ann_ret / mdd


def hit_rate(returns: pl.Series) -> float:
    r = returns.drop_nulls()
    if r.len() == 0:
        return float("nan")
    return _f((r > 0).mean())


def information_ratio(strategy: pl.Series, benchmark: pl.Series) -> float:
    aligned = pl.DataFrame({"s": strategy, "b": benchmark}).drop_nulls()
    if aligned.height < 2:
        return float("nan")
    active = aligned["s"] - aligned["b"]
    return sharpe_ratio(active)


def compute_metrics(
    daily: pl.DataFrame,
    *,
    ret_col: str = "net_ret",
    benchmark_col: str | None = "ew_ret",
) -> dict[str, float]:
    """Risk metrics from a daily returns frame."""
    rets = daily[ret_col]
    gross = daily["gross_ret"] if "gross_ret" in daily.columns else rets
    metrics: dict[str, float] = {
        "n_days": float(daily.height),
        "sharpe": sharpe_ratio(rets),
        "sortino": sortino_ratio(rets),
        "max_drawdown": max_drawdown(rets),
        "calmar": calmar_ratio(rets),
        "hit_rate": hit_rate(rets),
        "net_sharpe": sharpe_ratio(rets),
        "gross_sharpe": sharpe_ratio(gross),
        "avg_turnover": (
            _f(daily["turnover"].mean())
            if "turnover" in daily.columns
            else float("nan")
        ),
        "mean_net_ret": _f(rets.mean()) if rets.len() else float("nan"),
    }
    if benchmark_col and benchmark_col in daily.columns:
        metrics["ir_ew"] = information_ratio(rets, daily[benchmark_col])
    else:
        metrics["ir_ew"] = float("nan")
    return metrics


def _ew_benchmark(panel: pl.DataFrame) -> pl.DataFrame:
    return (
        panel.group_by("date")
        .agg(pl.col("ret_1d").mean().alias("ew_ret"))
        .sort("date")
    )


def prepare_panel(panel: pl.DataFrame, config: StrategyConfig) -> pl.DataFrame:
    """Features → labels → target weights on a research panel."""
    feat = build_features(panel, config)
    labeled = build_labels(feat, config)
    return build_target_weights(labeled, config)


def evaluate_strategy(
    panel: pl.DataFrame,
    config: StrategyConfig,
) -> tuple[pl.DataFrame, list[dict[str, float]], dict[str, float], float]:
    """Run full pipeline; return daily PnL, fold metrics, OOS metrics, capacity.

    Walk-forward uses the same rule-based weights everywhere; folds define the
    OOS reporting windows with embargo gaps so label horizons do not overlap
    train endpoints.
    """
    prepared = prepare_panel(panel, config)
    daily = apply_costs(prepared, config)
    ew = _ew_benchmark(prepared)
    daily = daily.join(ew, on="date", how="left")

    dates = prepared["date"].unique().sort().to_list()
    folds = purged_folds(dates, config.walk_forward)
    fold_metrics: list[dict[str, float]] = []
    oos_parts: list[pl.DataFrame] = []
    for i, fold in enumerate(folds):
        chunk = daily.filter(
            (pl.col("date") >= fold.test_start) & (pl.col("date") <= fold.test_end)
        )
        if chunk.height == 0:
            continue
        m = compute_metrics(chunk)
        m["fold"] = float(i)
        m["test_start"] = fold.test_start.toordinal()
        m["test_end"] = fold.test_end.toordinal()
        fold_metrics.append(m)
        oos_parts.append(chunk)

    if oos_parts:
        oos = pl.concat(oos_parts).unique(subset=["date"]).sort("date")
        oos_metrics = compute_metrics(oos)
    else:
        # Fallback: evaluate full sample when history is too short for folds.
        oos = daily
        oos_metrics = compute_metrics(oos)
        oos_metrics["short_history_fallback"] = 1.0

    capacity = estimate_capacity_usd(
        prepared,
        max_participation=config.max_participation,
    )
    # Attach equity curve helpers for notebooks.
    daily = daily.sort("date").with_columns(
        (1.0 + pl.col("net_ret").fill_null(0.0)).cum_prod().alias("equity_net"),
        (1.0 + pl.col("gross_ret").fill_null(0.0)).cum_prod().alias("equity_gross"),
    )
    return daily, fold_metrics, oos_metrics, capacity


def folds_respect_embargo(
    folds: list[FoldWindow],
    embargo_days: int,
    *,
    dates: list[date] | None = None,
) -> bool:
    """Validate train_end and test_start are separated by ≥ embargo trading days."""
    uniq = sorted(set(dates)) if dates is not None else None
    for fold in folds:
        if fold.test_start <= fold.train_end:
            return False
        if uniq is not None:
            try:
                i_train = uniq.index(fold.train_end)
                i_test = uniq.index(fold.test_start)
            except ValueError:
                return False
            if i_test - i_train - 1 < embargo_days:
                return False
        elif (fold.test_start - fold.train_end).days < embargo_days:
            return False
    return True


def min_calendar_gap(train_end: date, test_start: date) -> int:
    return (test_start - train_end).days


def shift_date(d: date, days: int) -> date:
    return d + timedelta(days=days)
