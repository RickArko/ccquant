# Repository Guidelines

## API Keys & Data Sources

All external API keys are loaded from `.env` via `python-dotenv`. Copy
`.env.example` to `.env` and fill in your keys. Full pricing tiers, free-tier
limits, and key setup instructions are in [`documentation/API_Pricing.md`](documentation/API_Pricing.md).

## Commands

```bash
uv sync --extra dev                       # optional alias; plain `uv sync` already installs lint/test + notebooks via dependency-groups.dev
uv sync --extra forecast                  # optional; forecast libs also in dependency-groups.dev
uv sync --extra dbt                       # + dbt transforms
uv run pre-commit install                 # install git hook (run once after clone)
uv run pre-commit run --all-files         # run all hooks manually
uv run pytest                             # all tests
uv run pytest tests/test_sources.py::test_fetch_binance_daily_parses_kline  # single test
uv run ruff check .                       # lint (line-length 88; rules E,F,I,B,UP,ASYNC)
uv run mypy src                           # typecheck — strict mode, MUST run on `src` not `.`
```

`pre-commit` runs `ruff check --fix` -> `mypy src` -> `pytest -q` on every commit
(hooks use `uv run` to reuse the project venv). Bypass with `git commit --no-verify`.

Verify in order: `ruff check .` -> `mypy src` -> `pytest` -> `dbt snapshot` -> `dbt build && dbt test` (dbt requires `--extra dbt`). Optional project lint: `dbt build --select package:dbt_project_evaluator --project-dir dbt --profiles-dir dbt`. Conventions: [`documentation/dbt_conventions.md`](documentation/dbt_conventions.md).

CLI entry point is `ccquant` (`ccquant.cli:main`), a Typer app with subcommands:

```bash
uv run ccquant sync all                    # one-command update: universe + daily + hourly + status
uv run ccquant sync universe [--size N] [--config FILE]   # fetch top-cap universe + probe exchange pairs
uv run ccquant sync backfill --interval {1d|1h} [--top N] [--full|--tail] [--force] [--config FILE]
uv run ccquant sync oi [--interval {1d|1h}] [--top N] [--full|--tail]   # open interest (Binance+Bybit+OKX)
uv run ccquant sync depth [--top N]        # CEX order-book depth snapshots (bps features)
uv run ccquant sync mev [--top N]          # DEX prices + local MEV-Boost parquet
uv run ccquant import mev-boost --source DIR  # land MEV-Boost parquet dumps
uv run ccquant sync macro                  # FRED macro series
uv run ccquant sync wallets --no-tail    # wallet registry only (start here; no RPC)
uv run ccquant sync wallets --full       # force historical extract
uv run ccquant sync wallets              # + RPC tail (needs dedicated solana_rpc_url)
uv run ccquant migrate onchain [--source FILE]  # migrate onchain.duckdb into main DB
uv run ccquant wallet discover --chain solana --top 20
uv run ccquant wallet discover --chain bitcoin --top 20
uv run ccquant wallet import-extract --source solarchive --date YYYY-MM-DD
uv run ccquant wallet import-extract --source bigquery --chain bitcoin
uv run ccquant wallet resolve-sns mitch.sol
uv run ccquant wallet alerts --since 1
uv run ccquant db backup [--dest DIR] [--keep N]  # timestamped file-copy backup
uv run ccquant status
uv run ccquant dashboard [--out PATH] [--no-open] [--live-interval 5m] [--no-live]  # Market Tracker HTML (+ near-live tape)
uv run ccquant sync onchain                              # blockchain.info + BID valuation
uv run ccquant sync etf                                  # Farside BTC ETF flows + Yahoo MSTR
uv run ccquant research run --strategy cs_mom_simple     # multi-year CS momentum (panel: daily)
uv run ccquant research run --strategy cs_mom_long_only  # CS mom long-only
uv run ccquant research run --strategy btc_ts_mom        # BTC dual time-series mom
uv run ccquant research run --strategy btc_macro_ls      # BTC-only macro long/short (~10y)
uv run ccquant research run --strategy cs_mom_oi_regime  # signals + OI + macro regime template
uv run python scripts/run_strategy_ladder.py             # pre-registered ladder → first PASS
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
uv run dbt build --select package:dbt_project_evaluator --project-dir dbt --profiles-dir dbt  # lint project structure
uv run python -m tests.fixtures.seed_dbt_fixture             # seed CI fixture data locally
```

`sync all` is the fastest way to bring the DB up to today — it runs universe
refresh, then tail-refreshes daily and hourly (only fetching recent candles,
not re-pulling full history), then OI + macro + wallets, then `dbt snapshot`
(SCD2 `snap_assets` → `dim_assets_history`) and `dbt build` to rebuild
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
  Use `--tail` to force a short refresh. Use `--force` with `--full` to ignore
  `backfill_complete` and re-pull full history (needed if an earlier incomplete
  sync was marked complete, or after Binance geo-blocks left a short series via
  Coinbase tail-only refreshes).

## Architecture

- `src/ccquant/`: `cli` (Typer), `config` (frozen dataclasses + YAML/env load), `models`
  (frozen dataclasses), `sources` (Binance/Coinbase/CoinGecko HTTP adapters), `storage`
  (DuckDB `MarketStore`), `sync` (`MarketSync` orchestration), `forecasting` (polars
  panel loaders for downstream models).
- Source preference is Binance -> Coinbase -> CoinGecko fallback, gated by
  `universe.source_preference`. **Hourly has no CoinGecko fallback** (daily only); a
  symbol with neither Binance pair nor Coinbase product yields zero hourly candles.
- DuckDB tables: `assets`, `ohlcv_daily`, `ohlcv_hourly`, `sync_state`,
  `onchain_series`, `onchain_sync_state`, `open_interest`,
  `order_book_snapshots`, `order_book_sync_state`, `dex_price_daily`,
  `mev_boost_payloads`, `macro_series`, `macro_sync_state`, `wallet_registry`,
  `wallet_transfers`, `wallet_positions_daily`, `wallet_sync_state`,
  `wallet_alerts`, `wallet_identities`, `wallet_identity_links`. Schema is
  created idempotently on `MarketStore` init. Wallet flow analytics use dbt
  `fct_wallet_signals_daily` (raw `wallet_signals_daily` is legacy/unused).
  `sync_state.earliest_at`/`latest_at` are stored as ISO varchar.
- dbt transformation layer lives in `dbt/`. Python owns `main` schema (raw);
  dbt owns `main_staging` (views), `main_marts` (tables), `main_signals`
  (tables), `main_events` (tables). dbt never writes to `main`.
- dbt profiles use `{{ env_var('CCQUANT_DB', 'data/ccquant.duckdb') }}` so the
  same DB path as the Python layer is used. Run dbt commands with
  `--project-dir dbt --profiles-dir dbt`.
- Open interest has per-exchange config toggles (`open_interest.binance/bybit/okx`).
  Disable any exchange in config without breaking the aggregate mart.
- Order-book depth mirrors OI toggles (`order_book.binance/bybit/okx`). Free REST
  books are live-only — `sync depth` self-records forward snapshots (bps-band
  features, not full L2 ladders). MEV/arb marts (`fct_cex_dex_basis`,
  `mart_mev_arb_daily`) are separate from `mart_signals_daily`.
- Optional extras: `uv sync --extra forecast` (numpy/pandas/scikit-learn/statsmodels) and
  `--extra notebook` (jupyterlab/plotly/ipykernel; also included in default
  `dependency-groups.dev` / `--extra dev`). `forecasting.py` itself uses only core deps
  (duckdb, polars) and is always importable; the heavier modeling libs are for notebooks
  and future model layers (installed by default via `uv sync`).

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
