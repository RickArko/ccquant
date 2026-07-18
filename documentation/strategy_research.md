# Strategy Research Contract

Reproducible framework for identifying **positive risk-adjusted** crypto opportunities
on local DuckDB panels. Implementation lives in `src/ccquant/strategy/`.

## Happy path — multi-year simple momentum

Short panels (weeks/months) are **not** a valid multi-year test. Force a full
daily backfill first, then evaluate `cs_mom_simple`:

```bash
uv run ccquant sync universe --size 50
uv run ccquant sync backfill --interval 1d --full --force --top 50
# Incremental marts only merge a 7-day tail — full-refresh after a deep backfill:
uv run dbt run --select fct_ohlcv_daily --full-refresh --project-dir dbt --profiles-dir dbt
uv run dbt snapshot --project-dir dbt --profiles-dir dbt
uv run dbt run --select dim_assets --project-dir dbt --profiles-dir dbt
uv run ccquant status   # majors should show years of history
uv run ccquant research run --strategy cs_mom_simple
# or: notebooks/Strategy_Template.ipynb
```

`cs_mom_simple` uses `panel: daily` (OHLCV via `load_daily_panel`) so price
momentum does not depend on sparse signals joins. CLI prints history span and
warns if `< 365` calendar days.

## Happy path — signals / regime strategy

```bash
uv run ccquant sync all   # refresh raw + dbt marts
uv run ccquant research run --strategy cs_mom_oi_regime
```

Panels come from documented loaders — never live HTTP inside the strategy layer
when store data exists.

## Framework stages

Every strategy must fill these stages (see YAML under `config/strategies/`):

| Stage | Requirement |
|---|---|
| 1. Hypothesis | Economic story, horizon, failure modes |
| 2. Universe | Active mart symbols; optional SCD2 as-of membership |
| 3. Features (PIT) | Only columns known at decision time `t`; lag macro/on-chain ≥1 day |
| 4. Labels | Forward returns at horizons `{5,20}`; primary = excess vs equal-weight |
| 5. Portfolio | CS rank → quintile L/S → dollar-neutral → vol-target gross |
| 6. Costs & capacity | Fee bps + slippage ∝ √(participation/ADV); missing ADV → fee-only |
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

## Example strategies

### `cs_mom_simple` (multi-year momentum)

Pure weekly CS long/short on 20d/60d momentum. No OI, regime disabled,
`panel: daily`, walk-forward 504/126/126. Config:
[`config/strategies/cs_mom_simple.yaml`](../config/strategies/cs_mom_simple.yaml).

### `cs_mom_oi_regime`

Weekly CS long/short: momentum + volume z + OI, gated by lagged macro liquidity.
Config:
[`config/strategies/cs_mom_oi_regime.yaml`](../config/strategies/cs_mom_oi_regime.yaml).

Pass criterion (both): OOS net Sharpe > 0 and IR(EW) > 0. A failed gate on a
correct multi-year sample is a valid research outcome (no edge), not a harness bug.

## Artifacts

`StrategyReport` includes config hash, data min/max dates, calendar span, fold
count, fold metrics, and pass/fail. Optional JSON under `data/research/`
(gitignored).
