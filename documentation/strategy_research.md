# Strategy Research Contract

Reproducible framework for identifying **positive risk-adjusted** crypto opportunities
on local DuckDB panels. Implementation lives in `src/ccquant/strategy/`.

## Happy path

```bash
uv run ccquant sync all   # refresh raw + dbt marts
uv run ccquant research run --strategy cs_mom_oi_regime
# or: notebooks/Strategy_Template.ipynb
```

Panels come from `load_signals_panel()` / related loaders — never live HTTP inside
the strategy layer when store data exists.

## Framework stages

Every strategy must fill these stages (see YAML under `config/strategies/`):

| Stage | Requirement |
|---|---|
| 1. Hypothesis | Economic story, horizon, failure modes |
| 2. Universe | Active mart symbols; optional SCD2 as-of membership |
| 3. Features (PIT) | Only info known at decision time `t`; lag macro/on-chain ≥1 day |
| 4. Labels | Forward returns at horizons `{5,20}`; primary = excess vs equal-weight |
| 5. Portfolio | CS rank → quintile L/S → dollar-neutral → vol-target gross |
| 6. Costs & capacity | Fee bps + slippage ∝ √(participation/ADV); ADV floor; turnover |
| 7. Evaluation | Purged walk-forward with embargo ≥ label horizon |
| 8. Scale gates | Pass only if OOS net Sharpe > 0, IR(EW) > 0, capacity headroom |

## Metrics

Reported on each OOS fold and pooled OOS:

- **Sharpe** / **Sortino** (annualized, √365 on daily crypto bars)
- **IR** vs equal-weight universe
- **Max drawdown**, **Calmar**
- **Hit-rate** (fraction of positive daily net returns)
- **Turnover** (average one-way weight change per rebalance)
- **Net Sharpe** after costs
- **Capacity** — max notional with participation ≤ configured % of ADV

## Leakage rules

1. On-chain / macro columns on `mart_signals_daily` are **date-global** — regime
   features only, not per-asset alpha unless proven asset-native.
2. Do **not** use `fct_btc_insider_timing` scores that embed forward returns.
3. Depth / MEV are optional overlays; not required for the default CS template.
4. Alignment: UTC dates; daily source priority Binance → Coinbase → CoinGecko.
5. Labels use close-to-close forward returns; features at `t` may only use data
   available at or before `t` (macro lagged by config).

## Example strategy: `cs_mom_oi_regime`

Weekly-rebalanced cross-sectional long/short: momentum + volume z + OI change,
gated by a lagged macro liquidity regime. Config:
[`config/strategies/cs_mom_oi_regime.yaml`](../config/strategies/cs_mom_oi_regime.yaml).

Pass criterion: OOS net Sharpe > 0 and IR(EW) > 0 on the holdout evaluation.

## Artifacts

`StrategyReport` includes config hash, data max-date, fold metrics, and pass/fail.
Optional JSON write under `data/research/` (gitignored).
