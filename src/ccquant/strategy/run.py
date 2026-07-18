"""Orchestrate strategy research runs from DuckDB panels or in-memory frames."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from ccquant.forecasting import load_daily_panel, load_signals_panel
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


@dataclass(frozen=True)
class PanelHistory:
    date_min: date | None
    date_max: date | None
    n_calendar_days: int
    n_symbols: int

    @property
    def is_multiyear(self) -> bool:
        return self.n_calendar_days >= 365


def panel_history(panel: pl.DataFrame) -> PanelHistory:
    if "date" not in panel.columns or panel.height == 0:
        return PanelHistory(None, None, 0, 0)
    dmin = panel["date"].min()
    dmax = panel["date"].max()
    n_sym = int(panel["symbol"].n_unique()) if "symbol" in panel.columns else 0
    if dmin is None or dmax is None:
        return PanelHistory(None, None, 0, n_sym)
    # Polars may return date or datetime-like; normalize via str→date when needed.
    if not isinstance(dmin, date):
        dmin = date.fromisoformat(str(dmin)[:10])
    if not isinstance(dmax, date):
        dmax = date.fromisoformat(str(dmax)[:10])
    return PanelHistory(
        date_min=dmin,
        date_max=dmax,
        n_calendar_days=(dmax - dmin).days + 1,
        n_symbols=n_sym,
    )


def load_strategy_panel(
    database: str | Path,
    config: StrategyConfig,
) -> pl.DataFrame:
    """Load the panel source declared by ``config.panel`` (daily | signals)."""
    if config.panel == "daily":
        return load_daily_panel(database)
    return load_signals_panel(database)


def run_strategy(
    database: str | Path | None = None,
    config_path: str | Path | None = None,
    *,
    config: StrategyConfig | None = None,
    panel: pl.DataFrame | None = None,
    write_dir: str | Path | None = None,
) -> StrategyReport:
    """Load panel → evaluate → gate → optional JSON artifact.

    Prefer an in-memory ``panel`` for tests. Otherwise load via
    ``config.panel`` (``daily`` or ``signals``).
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
        panel = load_strategy_panel(database, config)

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
    hist = panel_history(panel)
    if panel.height == 0:
        report = StrategyReport(
            strategy_name=config.name,
            config_hash=config.config_hash(),
            data_max_date=None,
            data_min_date=None,
            n_calendar_days=0,
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
    report = StrategyReport(
        strategy_name=config.name,
        config_hash=config.config_hash(),
        data_max_date=str(hist.date_max) if hist.date_max else None,
        data_min_date=str(hist.date_min) if hist.date_min else None,
        n_calendar_days=hist.n_calendar_days,
        passed=passed,
        oos_metrics=oos_metrics,
        fold_metrics=tuple(fold_metrics),
        capacity_usd=capacity,
        target_notional_usd=config.target_notional_usd,
        gate_reasons=reasons,
        n_symbols=hist.n_symbols,
        n_days=int(daily.height),
        n_folds=len(fold_metrics),
    )
    if write_dir is not None:
        out = Path(write_dir) / f"{config.name}_{report.config_hash}.json"
        report.write_json(out)
    return StrategyRun(report=report, daily=daily)
