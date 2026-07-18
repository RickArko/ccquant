#!/usr/bin/env python3
"""Run the pre-registered winning-strategy ladder until the first pass."""

from __future__ import annotations

import sys
from pathlib import Path

from ccquant.config import load_config
from ccquant.strategy import run_strategy_detailed
from ccquant.strategy.spec import default_strategy_config_path, load_strategy_config

LADDER = (
    "cs_mom_long_only",
    "cs_mom_btc_neutral",
    "btc_ts_mom",
    "btc_macro_long_only",
)


def main() -> int:
    cfg = load_config(None)
    write_dir = Path("data/research")
    results: list[tuple[str, bool, dict[str, float]]] = []
    for name in LADDER:
        path = default_strategy_config_path(name)
        if not path.is_file():
            print(f"SKIP {name}: missing {path}")
            continue
        strat = load_strategy_config(path)
        run = run_strategy_detailed(
            database=cfg.database,
            config=strat,
            write_dir=write_dir,
        )
        report = run.report
        m = report.oos_metrics
        results.append((name, report.passed, m))
        status = "PASSED" if report.passed else "FAILED"
        print(
            f"{status} {name}  net_sharpe={m.get('net_sharpe')}  "
            f"ir_ew={m.get('ir_ew')}  capacity={report.capacity_usd:.0f}  "
            f"folds={report.n_folds}  days={report.n_calendar_days}"
        )
        if report.passed:
            print(f"WINNER: {name}")
            return 0
    print("NO_WINNER")
    for name, passed, m in results:
        print(
            f"  {name}: passed={passed} "
            f"net_sharpe={m.get('net_sharpe')} ir_ew={m.get('ir_ew')}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
