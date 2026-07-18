"""Orchestrate strategy research runs from DuckDB panels or in-memory frames."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from ccquant.forecasting import load_signals_panel
from ccquant.strategy.evaluate import evaluate_strategy
from ccquant.strategy.report import StrategyReport, apply_gates
from ccquant.strategy.spec import (
    StrategyConfig,
    default_strategy_config_path,
    load_strategy_config,
)


@dataclass(frozen=True)
class StrategyRun:
    """Report plus daily PnL frame for charting."""

    report: StrategyReport
    daily: pl.DataFrame


def _panel_max_date(panel: pl.DataFrame) -> str | None:
    if "date" not in panel.columns or panel.height == 0:
        return None
    return str(panel["date"].max())


def run_strategy(
    database: str | Path | None = None,
    config_path: str | Path | None = None,
    *,
    config: StrategyConfig | None = None,
    panel: pl.DataFrame | None = None,
    write_dir: str | Path | None = None,
) -> StrategyReport:
    """Load panel → evaluate → gate → optional JSON artifact.

    Prefer an in-memory ``panel`` for tests. Otherwise load
    ``mart_signals_daily`` via ``load_signals_panel(database)``.
    """
    return run_strategy_detailed(
        database,
        config_path,
        config=config,
        panel=panel,
        write_dir=write_dir,
    ).report


def run_strategy_detailed(
    database: str | Path | None = None,
    config_path: str | Path | None = None,
    *,
    config: StrategyConfig | None = None,
    panel: pl.DataFrame | None = None,
    write_dir: str | Path | None = None,
) -> StrategyRun:
    """Same as ``run_strategy`` but also returns the daily PnL frame."""
    if config is None:
        path = Path(config_path) if config_path else default_strategy_config_path()
        config = load_strategy_config(path)

    if panel is None:
        if database is None:
            raise ValueError("database or panel is required")
        panel = load_signals_panel(database)

    empty_daily = pl.DataFrame(
        schema={
            "date": pl.Date,
            "gross_ret": pl.Float64,
            "net_ret": pl.Float64,
            "turnover": pl.Float64,
            "equity_net": pl.Float64,
            "equity_gross": pl.Float64,
        }
    )
    if panel.height == 0:
        report = StrategyReport(
            strategy_name=config.name,
            config_hash=config.config_hash(),
            data_max_date=None,
            passed=False,
            gate_reasons=("empty panel",),
        )
        return StrategyRun(report=report, daily=empty_daily)

    daily, fold_metrics, oos_metrics, capacity = evaluate_strategy(panel, config)
    passed, reasons = apply_gates(
        oos_metrics=oos_metrics,
        capacity_usd=capacity,
        target_notional_usd=config.target_notional_usd,
        min_net_sharpe=config.gates.min_net_sharpe,
        min_ir=config.gates.min_ir,
    )
    n_symbols = int(panel["symbol"].n_unique()) if "symbol" in panel.columns else 0
    report = StrategyReport(
        strategy_name=config.name,
        config_hash=config.config_hash(),
        data_max_date=_panel_max_date(panel),
        passed=passed,
        oos_metrics=oos_metrics,
        fold_metrics=tuple(fold_metrics),
        capacity_usd=capacity,
        target_notional_usd=config.target_notional_usd,
        gate_reasons=reasons,
        n_symbols=n_symbols,
        n_days=int(daily.height),
    )
    if write_dir is not None:
        out = Path(write_dir) / f"{config.name}_{report.config_hash}.json"
        report.write_json(out)
    return StrategyRun(report=report, daily=daily)
