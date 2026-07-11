# Repository Guidelines

## API Keys & Data Sources

All external API keys are loaded from `.env` via `python-dotenv`. Copy
`.env.example` to `.env` and fill in your keys. Full pricing tiers, free-tier
limits, and key setup instructions are in [`documentation/API_Pricing.md`](documentation/API_Pricing.md).

## Commands

```bash
uv sync --extra dev                       # lint/test tooling (mypy, pytest, ruff, pre-commit)
uv sync --extra dev --extra notebook --extra forecast  # + Jupyter/plotly + modeling libs for notebooks
uv sync --extra dev --extra notebook --extra forecast --extra dbt  # + dbt transforms
uv run pre-commit install                 # install git hook (run once after clone)
uv run pre-commit run --all-files         # run all hooks manually
uv run pytest                             # all tests
uv run pytest tests/test_sources.py::test_fetch_binance_daily_parses_kline  # single test
uv run ruff check .                       # lint (line-length 88; rules E,F,I,B,UP,ASYNC)
uv run mypy src                           # typecheck — strict mode, MUST run on `src` not `.`
```

`pre-commit` runs `ruff check --fix` -> `mypy src` -> `pytest -q` on every commit
(hooks use `uv run` to reuse the project venv). Bypass with `git commit --no-verify`.

Verify in order: `ruff check .` -> `mypy src` -> `pytest` -> `dbt build && dbt test` (dbt requires `--extra dbt`).

CLI entry point is `ccquant` (`ccquant.cli:main`), a Typer app with subcommands:

```bash
uv run ccquant sync all                    # one-command update: universe + daily + hourly + status
uv run ccquant sync universe [--size N] [--config FILE]   # fetch top-cap universe + probe exchange pairs
uv run ccquant sync backfill --interval {1d|1h} [--top N] [--full|--tail] [--config FILE]
uv run ccquant sync oi [--interval {1d|1h}] [--top N] [--full|--tail]   # open interest (Binance+Bybit+OKX)
uv run ccquant sync macro                  # FRED macro series
uv run ccquant sync wallets [--full|--no-tail]  # wallet registry + history + tail
uv run ccquant migrate onchain [--source FILE]  # migrate onchain.duckdb into main DB
uv run ccquant wallet discover --chain solana --top 20
uv run ccquant wallet import-extract --source solarchive --date YYYY-MM-DD
uv run ccquant wallet resolve-sns mitch.sol
uv run ccquant wallet alerts --since 1
uv run ccquant db backup [--dest DIR] [--keep N]  # timestamped file-copy backup
uv run ccquant status
uv run ccquant export parquet --out data/export
uv run ccquant export csv --out data/export
```

### dbt commands

```bash
uv sync --extra dbt                        # install dbt-core + dbt-duckdb
uv run dbt deps --project-dir dbt --profiles-dir dbt          # install dbt packages (dbt_utils)
uv run dbt seed --project-dir dbt --profiles-dir dbt          # load seeds/events.csv
uv run dbt build --project-dir dbt --profiles-dir dbt         # build all models + run all tests
uv run dbt test --project-dir dbt --profiles-dir dbt          # run tests only
uv run dbt snapshot --project-dir dbt --profiles-dir dbt      # run SCD2 snapshots
```

`sync all` is the fastest way to bring the DB up to today — it runs universe
refresh, then tail-refreshes daily and hourly (only fetching recent candles,
not re-pulling full history), then OI + macro + wallets, then `dbt build` to rebuild
marts/signals/events. Use it for routine updates. Use `--no-dbt` to skip the
dbt step (e.g. when dbt isn't installed). Use `--no-wallets` to skip wallet sync.

`sync universe` marks all previously active assets inactive before inserting the new set.

## Runtime & Config

- DB path defaults to `data/ccquant.duckdb`; override with `CCQUANT_DB` env var or a YAML
  config file passed via `--config/-c`. With no config file, built-in defaults apply
  (see `config/example.yaml` for the shape).
- `sync backfill` auto-runs `sync universe` when no active assets exist yet.
- `backfill --full` (default) only does a full historical pull when
  `sync_state.backfill_complete` is false; once complete it falls back to tail-refresh.
  Use `--tail` to force a short refresh.

## Architecture

- `src/ccquant/`: `cli` (Typer), `config` (frozen dataclasses + YAML/env load), `models`
  (frozen dataclasses), `sources` (Binance/Coinbase/CoinGecko HTTP adapters), `storage`
  (DuckDB `MarketStore`), `sync` (`MarketSync` orchestration), `forecasting` (polars
  panel loaders for downstream models).
- Source preference is Binance -> Coinbase -> CoinGecko fallback, gated by
  `universe.source_preference`. **Hourly has no CoinGecko fallback** (daily only); a
  symbol with neither Binance pair nor Coinbase product yields zero hourly candles.
- DuckDB tables: `assets`, `ohlcv_daily`, `ohlcv_hourly`, `sync_state`,
  `onchain_series`, `onchain_sync_state`, `open_interest`, `macro_series`,
  `macro_sync_state`, `wallet_registry`, `wallet_transfers`,
  `wallet_positions_daily`, `wallet_sync_state`, `wallet_signals_daily`,
  `wallet_alerts`. Schema is created idempotently on `MarketStore` init.
  `sync_state.earliest_at`/`latest_at` are stored as ISO varchar.
- dbt transformation layer lives in `dbt/`. Python owns `main` schema (raw);
  dbt owns `main_staging` (views), `main_marts` (tables), `main_signals`
  (tables), `main_events` (tables). dbt never writes to `main`.
- dbt profiles use `{{ env_var('CCQUANT_DB', 'data/ccquant.duckdb') }}` so the
  same DB path as the Python layer is used. Run dbt commands with
  `--project-dir dbt --profiles-dir dbt`.
- Open interest has per-exchange config toggles (`open_interest.binance/bybit/okx`).
  Disable any exchange in config without breaking the aggregate mart.
- Optional extras: `uv sync --extra forecast` (numpy/pandas/scikit-learn/statsmodels) and
  `--extra notebook` (jupyterlab/plotly). `forecasting.py` itself uses only core deps
  (duckdb, polars) and is always importable; the heavier modeling libs are for future
  model layers.

## Coding Style

Python 3.12+, frozen dataclasses for immutable records, explicit type annotations. mypy
runs in `strict` mode — keep it clean. Keep ingestion deterministic and side-effect-light;
add heavier modeling libraries behind optional extras, not core deps.

## Testing

- `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"`. Mock HTTP clients for source
  adapters (patch `client.get` with `AsyncMock`, or `monkeypatch.setattr` on `MarketSync`);
  never require live API calls in unit tests.
- Tests rely on `tmp_path` DuckDB files; no shared DB state. Name tests by behavior, e.g.
  `test_fetch_binance_daily_parses_kline`.

## Data Discipline

Do not commit databases, exports, credentials, or notebook outputs. `.gitignore` covers
`data/`, `*.duckdb`, `*.duckdb.wal`, `*.parquet`. Reproducibility flows through CLI
commands and `config/` — not committed binary data.
