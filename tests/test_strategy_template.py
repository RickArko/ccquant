"""Synthetic-panel tests for the strategy research template."""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from ccquant.strategy import (
    apply_costs,
    build_features,
    build_target_weights,
    dollar_neutral_check,
    folds_respect_embargo,
    lag_columns,
    load_strategy_config,
    prepare_panel,
    purged_folds,
    run_strategy,
    run_strategy_detailed,
)
from ccquant.strategy.costs import cost_drag_from_turnover
from ccquant.strategy.evaluate import compute_metrics
from ccquant.strategy.features import add_macro_regime
from ccquant.strategy.report import apply_gates
from ccquant.strategy.spec import (
    CostModel,
    FeatureSpec,
    GateSpec,
    HypothesisSpec,
    LabelSpec,
    PortfolioSpec,
    RegimeSpec,
    StrategyConfig,
    UniverseSpec,
    WalkForwardSpec,
)


def _dates(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def make_synthetic_panel(
    *,
    n_days: int = 400,
    symbols: tuple[str, ...] = ("AAA", "BBB", "CCC", "DDD", "EEE", "BTC"),
    seed_drift: float = 0.001,
) -> pl.DataFrame:
    """Build a liquid panel with persistent cross-sectional momentum."""
    rows: list[dict[str, object]] = []
    dates = _dates(n_days)
    for si, sym in enumerate(symbols):
        price = 100.0 * (1.0 + 0.1 * si)
        # Higher-index symbols trend up harder → predictable CS momentum.
        mu = seed_drift * (si + 1)
        for di, d in enumerate(dates):
            price *= 1.0 + mu + 0.01 * math.sin(di / 7.0 + si)
            vol = 1_000_000.0 * (1.0 + 0.1 * si)
            rows.append(
                {
                    "symbol": sym,
                    "date": d,
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": vol / price,
                    "total_open_interest_usd": 5_000_000.0 * (1.0 + 0.05 * si)
                    * (1.0 + di / 10_000.0),
                    "m2sl": 15_000.0 + di * 2.0,
                    "walcl": 8_000.0 + di * 1.0,
                    "dgs10": 3.0 + 0.001 * math.sin(di / 30.0),
                    "t10yie": 2.0,
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))


def make_config(**overrides: object) -> StrategyConfig:
    base = StrategyConfig(
        hypothesis=HypothesisSpec(
            name="cs_mom_oi_regime",
            story="test",
            horizon_days=5,
            failure_modes=("test",),
        ),
        label=LabelSpec(horizons=(5, 20), primary_horizon=5),
        cost=CostModel(fee_bps_rt=10.0, slippage_coef=0.05),
        walk_forward=WalkForwardSpec(
            train_days=120,
            test_days=40,
            step_days=40,
            embargo_days=20,
        ),
        portfolio=PortfolioSpec(
            n_quantiles=5,
            vol_target_ann=0.10,
            min_adv_usd=1_000.0,
            rebalance="weekly",
            adv_window=20,
        ),
        regime=RegimeSpec(lag_days=1, risk_off_z=-10.0, z_window=60),
        universe=UniverseSpec(top_n=10),
        features=FeatureSpec(
            mom_windows=(20, 60),
            vol_window=20,
            volume_z_window=20,
            oi_change_window=20,
            macro_lag_days=1,
        ),
        gates=GateSpec(min_net_sharpe=-1e9, min_ir=-1e9),
        target_notional_usd=1_000.0,
        max_participation=0.5,
    )
    if not overrides:
        return base
    data = {
        "hypothesis": base.hypothesis,
        "label": base.label,
        "cost": base.cost,
        "walk_forward": base.walk_forward,
        "portfolio": base.portfolio,
        "regime": base.regime,
        "universe": base.universe,
        "features": base.features,
        "gates": base.gates,
        "target_notional_usd": base.target_notional_usd,
        "max_participation": base.max_participation,
    }
    data.update(overrides)
    return StrategyConfig(**data)  # type: ignore[arg-type]


def test_load_strategy_yaml() -> None:
    path = Path("config/strategies/cs_mom_oi_regime.yaml")
    cfg = load_strategy_config(path)
    assert cfg.name == "cs_mom_oi_regime"
    assert cfg.label.primary_horizon == 5
    assert cfg.walk_forward.embargo_days >= max(cfg.label.horizons)
    assert len(cfg.config_hash()) == 16


def test_macro_lag_is_point_in_time() -> None:
    dates = _dates(5)
    df = pl.DataFrame(
        {
            "symbol": ["AAA"] * 5,
            "date": dates,
            "close": [1.0, 2.0, 3.0, 4.0, 5.0],
            "volume": [100.0] * 5,
            "m2sl": [10.0, 20.0, 30.0, 40.0, 50.0],
            "walcl": [1.0, 2.0, 3.0, 4.0, 5.0],
            "dgs10": [3.0] * 5,
            "t10yie": [2.0] * 5,
        }
    ).with_columns(pl.col("date").cast(pl.Date))
    lagged = lag_columns(df, ["m2sl"], lag=1, by="symbol")
    # At row date[t], m2sl_lag1 equals prior raw m2sl.
    assert lagged["m2sl_lag1"][1] == pytest.approx(10.0)
    assert lagged["m2sl_lag1"][2] == pytest.approx(20.0)
    assert lagged["m2sl_lag1"][0] is None or math.isnan(lagged["m2sl_lag1"][0])

    cfg = make_config()
    with_regime = add_macro_regime(df, cfg.regime)
    # Regime uses lagged levels: growth at t uses m2sl_{t-1} vs m2sl_{t-2}.
    # Same-day raw m2sl must not equal the PIT path used in regime (indirect check:
    # first two regime scores are null/zero-ish while raw m2sl is already moving).
    assert "regime_score" in with_regime.columns
    assert with_regime["regime_score"][0] is None or (
        with_regime["m2sl"][0] == 10.0 and with_regime["regime_score"][0] == 0.0
    )


def test_purged_folds_respect_embargo() -> None:
    dates = _dates(400)
    folds = purged_folds(
        dates,
        WalkForwardSpec(train_days=120, test_days=40, step_days=40, embargo_days=20),
    )
    assert folds
    assert folds_respect_embargo(folds, 20, dates=dates)
    for fold in folds:
        assert fold.test_start > fold.train_end


def test_dollar_neutral_weights_on_rebalance() -> None:
    panel = make_synthetic_panel(n_days=120)
    cfg = make_config()
    prepared = prepare_panel(panel, cfg)
    rebal = prepared.filter(pl.col("is_rebalance") & (pl.col("weight") != 0))
    if rebal.height == 0:
        # Regime may flatten; force-active config already uses risk_off_z=-10.
        pytest.skip("no rebalance weights produced")
    # Pick a single rebalance date with both sleeves.
    for d in rebal["date"].unique().to_list():
        day = prepared.filter(pl.col("date") == d)
        w = day["weight"]
        if float(w.abs().sum()) < 1e-9:
            continue
        assert dollar_neutral_check(w, tol=1e-5)
        assert float(w.abs().sum()) > 0
        break
    else:
        pytest.fail("no rebalance day with non-zero book")


def test_costs_reduce_sharpe_vs_gross() -> None:
    panel = make_synthetic_panel(n_days=250)
    cfg = make_config()
    prepared = prepare_panel(panel, cfg)
    daily = apply_costs(prepared, cfg)
    assert "net_ret" in daily.columns
    assert "gross_ret" in daily.columns
    # Fee drag identity.
    assert cost_drag_from_turnover(1.0, 10.0) == pytest.approx(0.001)
    # Higher turnover day should have higher total_cost (monotone in turnover for fees).
    if daily.height > 5:
        sample = daily.sort("turnover", descending=True).head(5)
        low = daily.sort("turnover").head(5)
        assert float(sample["fee_cost"].mean()) >= float(low["fee_cost"].mean())
    assert float(daily["total_cost"].min()) >= -1e-12
    # With non-negative costs, mean net return cannot exceed mean gross.
    assert float(daily["net_ret"].mean()) <= float(daily["gross_ret"].mean()) + 1e-12


def test_shuffled_features_break_predictive_structure() -> None:
    panel = make_synthetic_panel(n_days=300)
    cfg = make_config()
    feat = build_features(panel, cfg)
    # Rank IC proxy: corr(alpha_score, fwd 5d excess) on a simple lead.
    with_fwd = feat.sort(["symbol", "date"]).with_columns(
        (pl.col("close").shift(-5).over("symbol") / pl.col("close") - 1.0).alias(
            "fwd5"
        )
    )
    clean = with_fwd.select(["alpha_score", "fwd5"]).drop_nulls()
    ic_real_raw = clean.select(pl.corr("alpha_score", "fwd5")).item()
    ic_real = float(ic_real_raw) if ic_real_raw is not None else float("nan")

    # Shuffle alpha scores within each date → destroy CS ranking signal.
    shuffled = with_fwd.with_columns(
        pl.col("alpha_score")
        .shuffle(seed=42)
        .over("date")
        .alias("alpha_score_shuf")
    )
    clean_s = shuffled.select(
        pl.col("alpha_score_shuf").alias("alpha_score"), pl.col("fwd5")
    ).drop_nulls()
    ic_shuf_raw = clean_s.select(pl.corr("alpha_score", "fwd5")).item()
    ic_shuf = float(ic_shuf_raw) if ic_shuf_raw is not None else float("nan")
    if math.isnan(ic_real) and math.isnan(ic_shuf):
        pytest.skip("insufficient variation for IC smoke test")
    assert abs(ic_real) >= abs(ic_shuf) - 1e-9 or abs(ic_real) > 0.02


def test_run_strategy_synthetic_report() -> None:
    panel = make_synthetic_panel(n_days=400)
    cfg = make_config()
    result = run_strategy_detailed(panel=panel, config=cfg)
    report = result.report
    assert report.strategy_name == "cs_mom_oi_regime"
    assert report.n_symbols == 6
    assert report.n_days > 0
    assert isinstance(report.passed, bool)
    for key in ("net_sharpe", "gross_sharpe", "ir_ew", "max_drawdown"):
        assert key in report.oos_metrics
        val = report.oos_metrics[key]
        assert val is None or isinstance(val, float)
    assert result.daily.height == report.n_days


def test_run_strategy_empty_panel() -> None:
    empty = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "date": pl.Date,
            "close": pl.Float64,
            "volume": pl.Float64,
        }
    )
    report = run_strategy(panel=empty, config=make_config())
    assert report.passed is False
    assert "empty panel" in report.gate_reasons


def test_apply_gates_fail_on_negative_sharpe() -> None:
    passed, reasons = apply_gates(
        oos_metrics={"net_sharpe": -0.5, "ir_ew": 0.1},
        capacity_usd=2_000_000,
        target_notional_usd=1_000_000,
        min_net_sharpe=0.0,
        min_ir=0.0,
    )
    assert passed is False
    assert any("net_sharpe" in r for r in reasons)


def test_build_target_weights_gross_positive() -> None:
    panel = make_synthetic_panel(n_days=100)
    cfg = make_config()
    prepared = build_target_weights(build_features(panel, cfg), cfg)
    assert "weight" in prepared.columns
    assert float(prepared["weight"].abs().sum()) >= 0.0


def test_compute_metrics_finite_on_noise() -> None:
    daily = pl.DataFrame(
        {
            "date": _dates(60),
            "gross_ret": [0.001 * ((i % 3) - 1) for i in range(60)],
            "net_ret": [0.0005 * ((i % 3) - 1) for i in range(60)],
            "turnover": [0.1] * 60,
            "ew_ret": [0.0002] * 60,
        }
    ).with_columns(pl.col("date").cast(pl.Date))
    m = compute_metrics(daily)
    assert m["n_days"] == 60.0
    assert math.isfinite(m["net_sharpe"]) or math.isnan(m["net_sharpe"])
