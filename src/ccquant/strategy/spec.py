"""Frozen strategy configuration dataclasses and YAML loader."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


@dataclass(frozen=True)
class HypothesisSpec:
    name: str
    story: str
    horizon_days: int
    failure_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LabelSpec:
    horizons: tuple[int, ...] = (5, 20)
    primary_horizon: int = 5


@dataclass(frozen=True)
class CostModel:
    fee_bps_rt: float = 10.0
    slippage_coef: float = 0.10


@dataclass(frozen=True)
class WalkForwardSpec:
    train_days: int = 252
    test_days: int = 63
    step_days: int = 63
    embargo_days: int = 20


@dataclass(frozen=True)
class PortfolioSpec:
    n_quantiles: int = 5
    vol_target_ann: float = 0.10
    min_adv_usd: float = 1_000_000.0
    rebalance: str = "weekly"
    adv_window: int = 20


@dataclass(frozen=True)
class RegimeSpec:
    lag_days: int = 1
    risk_off_z: float = -0.5
    z_window: int = 60


@dataclass(frozen=True)
class UniverseSpec:
    top_n: int = 50


@dataclass(frozen=True)
class FeatureSpec:
    mom_windows: tuple[int, ...] = (20, 60)
    vol_window: int = 20
    volume_z_window: int = 20
    oi_change_window: int = 20
    macro_lag_days: int = 1


@dataclass(frozen=True)
class GateSpec:
    min_net_sharpe: float = 0.0
    min_ir: float = 0.0


@dataclass(frozen=True)
class StrategyConfig:
    hypothesis: HypothesisSpec
    label: LabelSpec = field(default_factory=LabelSpec)
    cost: CostModel = field(default_factory=CostModel)
    walk_forward: WalkForwardSpec = field(default_factory=WalkForwardSpec)
    portfolio: PortfolioSpec = field(default_factory=PortfolioSpec)
    regime: RegimeSpec = field(default_factory=RegimeSpec)
    universe: UniverseSpec = field(default_factory=UniverseSpec)
    features: FeatureSpec = field(default_factory=FeatureSpec)
    gates: GateSpec = field(default_factory=GateSpec)
    target_notional_usd: float = 1_000_000.0
    max_participation: float = 0.01

    @property
    def name(self) -> str:
        return self.hypothesis.name

    def config_hash(self) -> str:
        payload = yaml.safe_dump(asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _as_tuple_int(value: Any, default: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return default
    return tuple(int(x) for x in value)


def _as_tuple_str(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(str(x) for x in value)


def load_strategy_config(path: str | Path) -> StrategyConfig:
    """Load a strategy YAML into a frozen ``StrategyConfig``."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    hyp = raw.get("hypothesis") or {}
    label = raw.get("label") or {}
    cost = raw.get("cost") or {}
    wf = raw.get("walk_forward") or {}
    port = raw.get("portfolio") or {}
    regime = raw.get("regime") or {}
    universe = raw.get("universe") or {}
    features = raw.get("features") or {}
    gates = raw.get("gates") or {}

    name = str(hyp.get("name") or raw.get("name") or Path(path).stem)
    horizons = _as_tuple_int(label.get("horizons"), (5, 20))
    primary = int(label.get("primary_horizon", horizons[0]))
    if primary not in horizons:
        horizons = tuple(sorted({*horizons, primary}))

    embargo = int(wf.get("embargo_days", max(horizons)))
    if embargo < max(horizons):
        embargo = max(horizons)

    return StrategyConfig(
        hypothesis=HypothesisSpec(
            name=name,
            story=str(hyp.get("story") or ""),
            horizon_days=int(hyp.get("horizon_days", primary)),
            failure_modes=_as_tuple_str(hyp.get("failure_modes")),
        ),
        label=LabelSpec(horizons=horizons, primary_horizon=primary),
        cost=CostModel(
            fee_bps_rt=float(cost.get("fee_bps_rt", 10.0)),
            slippage_coef=float(cost.get("slippage_coef", 0.10)),
        ),
        walk_forward=WalkForwardSpec(
            train_days=int(wf.get("train_days", 252)),
            test_days=int(wf.get("test_days", 63)),
            step_days=int(wf.get("step_days", 63)),
            embargo_days=embargo,
        ),
        portfolio=PortfolioSpec(
            n_quantiles=int(port.get("n_quantiles", 5)),
            vol_target_ann=float(port.get("vol_target_ann", 0.10)),
            min_adv_usd=float(port.get("min_adv_usd", 1_000_000.0)),
            rebalance=str(port.get("rebalance", "weekly")),
            adv_window=int(port.get("adv_window", 20)),
        ),
        regime=RegimeSpec(
            lag_days=int(regime.get("lag_days", features.get("macro_lag_days", 1))),
            risk_off_z=float(regime.get("risk_off_z", -0.5)),
            z_window=int(regime.get("z_window", 60)),
        ),
        universe=UniverseSpec(top_n=int(universe.get("top_n", 50))),
        features=FeatureSpec(
            mom_windows=_as_tuple_int(features.get("mom_windows"), (20, 60)),
            vol_window=int(features.get("vol_window", 20)),
            volume_z_window=int(features.get("volume_z_window", 20)),
            oi_change_window=int(features.get("oi_change_window", 20)),
            macro_lag_days=int(features.get("macro_lag_days", 1)),
        ),
        gates=GateSpec(
            min_net_sharpe=float(gates.get("min_net_sharpe", 0.0)),
            min_ir=float(gates.get("min_ir", 0.0)),
        ),
        target_notional_usd=float(raw.get("target_notional_usd", 1_000_000.0)),
        max_participation=float(raw.get("max_participation", 0.01)),
    )


def default_strategy_config_path(name: str = "cs_mom_oi_regime") -> Path:
    """Resolve ``config/strategies/{name}.yaml`` from the repo root heuristic."""
    here = Path.cwd()
    candidate = here / "config" / "strategies" / f"{name}.yaml"
    if candidate.is_file():
        return candidate
    for parent in here.parents:
        candidate = parent / "config" / "strategies" / f"{name}.yaml"
        if candidate.is_file():
            return candidate
    return here / "config" / "strategies" / f"{name}.yaml"
