# ccquant

Lightweight crypto market data and forecasting research toolkit.

The first goal is reproducible OHLCV collection into a local DuckDB database.
Forecasting code can then read the same tables for short-term and long-term
statistical, ML, and foundation-model experiments.

## Quickstart

```bash
uv sync --extra dev
uv run ccquant sync all                     # one-command update: universe + daily + hourly + status
uv run ccquant sync universe --size 100
uv run ccquant sync backfill --interval 1d
uv run ccquant sync backfill --interval 1h --top 10
uv run ccquant status
```

By default, data is stored at `data/ccquant.duckdb`. Override it with:

```bash
export CCQUANT_DB=data/research.duckdb
```

## Data Model

Primary tables:

- `assets`: active research universe with CoinGecko IDs and exchange pairs.
- `ohlcv_daily`: daily OHLCV candles by `symbol`, `date`, and `source`.
- `ohlcv_hourly`: hourly OHLCV candles by `symbol`, `hour`, and `source`.
- `sync_state`: per-symbol sync metadata.

Sources are tried in this order: Binance, Coinbase, then CoinGecko fallback.

## Export

```bash
uv run ccquant export parquet --out data/export
uv run ccquant export csv --out data/export
```

These exports are intended as stable inputs for notebooks, model training, and
external forecast pipelines.

## Forecasting Direction

Keep data ingestion deterministic and boring. Add models in layers:

1. Statistical baselines: naive, moving average, ARIMA/SARIMAX, volatility models.
2. ML features: lagged returns, rolling volatility, volume features, cross-asset ranks.
3. Foundation models: convert OHLCV panels into documented time-series prompts or
   dataset artifacts without coupling them to the ingestion code.
