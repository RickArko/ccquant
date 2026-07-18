"""Strategy research template: PIT features, portfolios, walk-forward evaluation."""

from ccquant.strategy.costs import apply_costs, estimate_capacity_usd
from ccquant.strategy.evaluate import (
    compute_metrics,
    evaluate_strategy,
    folds_respect_embargo,
    prepare_panel,
    purged_folds,
)
from ccquant.strategy.features import build_features, lag_columns
from ccquant.strategy.labels import build_labels
from ccquant.strategy.portfolio import (
    build_target_weights,
    dollar_neutral_check,
    gross_exposure,
)
from ccquant.strategy.report import StrategyReport, apply_gates
from ccquant.strategy.run import StrategyRun, run_strategy, run_strategy_detailed
from ccquant.strategy.spec import StrategyConfig, load_strategy_config

__all__ = [
    "StrategyConfig",
    "StrategyReport",
    "StrategyRun",
    "apply_costs",
    "apply_gates",
    "build_features",
    "build_labels",
    "build_target_weights",
    "compute_metrics",
    "dollar_neutral_check",
    "estimate_capacity_usd",
    "evaluate_strategy",
    "folds_respect_embargo",
    "gross_exposure",
    "lag_columns",
    "load_strategy_config",
    "prepare_panel",
    "purged_folds",
    "run_strategy",
    "run_strategy_detailed",
]
