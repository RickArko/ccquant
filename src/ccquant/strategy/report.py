"""Structured strategy research reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # noqa: PLR0124
            return None
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@dataclass(frozen=True)
class StrategyReport:
    strategy_name: str
    config_hash: str
    data_max_date: str | None
    passed: bool
    data_min_date: str | None = None
    n_calendar_days: int = 0
    oos_metrics: dict[str, float] = field(default_factory=dict)
    fold_metrics: tuple[dict[str, float], ...] = ()
    capacity_usd: float = 0.0
    target_notional_usd: float = 0.0
    gate_reasons: tuple[str, ...] = ()
    n_symbols: int = 0
    n_days: int = 0
    n_folds: int = 0

    def to_dict(self) -> dict[str, Any]:
        result = _json_safe(asdict(self))
        assert isinstance(result, dict)
        return result

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def write_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_json() + "\n")
        return out


def apply_gates(
    *,
    oos_metrics: dict[str, float],
    capacity_usd: float,
    target_notional_usd: float,
    min_net_sharpe: float,
    min_ir: float,
) -> tuple[bool, tuple[str, ...]]:
    """Scale gates: net Sharpe, IR vs EW, capacity headroom."""
    reasons: list[str] = []
    net_sharpe = oos_metrics.get("net_sharpe", float("nan"))
    ir = oos_metrics.get("ir_ew", float("nan"))
    if net_sharpe != net_sharpe or net_sharpe <= min_net_sharpe:
        reasons.append(
            f"net_sharpe {net_sharpe} failed gate > {min_net_sharpe}"
        )
    if ir != ir or ir <= min_ir:
        reasons.append(f"ir_ew {ir} failed gate > {min_ir}")
    if capacity_usd < target_notional_usd:
        reasons.append(
            f"capacity_usd {capacity_usd:.0f} "
            f"< target_notional {target_notional_usd:.0f}"
        )
    return (len(reasons) == 0, tuple(reasons))
