# Repository Guidelines

## Commands

```bash
uv sync --extra dev                       # install dev tooling (mypy, pytest, ruff)
uv run pytest                             # all tests
uv run pytest tests/test_sources.py::test_fetch_binance_daily_parses_kline  # single test
uv run ruff check .                       # lint (line-length 88; rules E,F,I,B,UP,ASYNC)
uv run mypy src                           # typecheck — strict mode, MUST run on `src` not `.`
```

Verify in order: `ruff check .` -> `mypy src` -> `pytest`.

CLI entry point is `ccquant` (`ccquant.cli:main`), a Typer app with subcommands:

```bash
uv run ccquant sync universe [--size N] [--config FILE]   # fetch top-cap universe + probe exchange pairs
uv run ccquant sync backfill --interval {1d|1h} [--top N] [--full|--tail] [--config FILE]
uv run ccquant status
uv run ccquant export parquet --out data/export
uv run ccquant export csv --out data/export
```

## Runtime & Config

- DB path defaults to `data/ccquant.duckdb`; override with `CCQUANT_DB` env var or a YAML
  config file passed via `--config/-c`. With no config file, built-in defaults apply
  (see `config/example.yaml` for the shape).
- `sync backfill` auto-runs `sync universe` when no active assets exist yet.
- `backfill --full` (default) only does a full historical pull when
  `sync_state.backfill_complete` is false; once complete it falls back to tail-refresh.
  Use `--tail` to force a short refresh.
- `sync universe` marks all previously active assets inactive before inserting the new set.

## Architecture

- `src/ccquant/`: `cli` (Typer), `config` (frozen dataclasses + YAML/env load), `models`
  (frozen dataclasses), `sources` (Binance/Coinbase/CoinGecko HTTP adapters), `storage`
  (DuckDB `MarketStore`), `sync` (`MarketSync` orchestration), `forecasting` (polars
  panel loaders for downstream models).
- Source preference is Binance -> Coinbase -> CoinGecko fallback, gated by
  `universe.source_preference`. **Hourly has no CoinGecko fallback** (daily only); a
  symbol with neither Binance pair nor Coinbase product yields zero hourly candles.
- DuckDB tables: `assets`, `ohlcv_daily`, `ohlcv_hourly`, `sync_state`. Schema is created
  idempotently on `MarketStore` init. `sync_state.earliest_at`/`latest_at` are stored as
  ISO varchar.
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
