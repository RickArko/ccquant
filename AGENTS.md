# Repository Guidelines

## Project Structure

- `src/ccquant/`: package code for config, market sources, DuckDB storage, sync
  orchestration, CLI commands, and forecasting data loaders.
- `tests/`: focused unit tests for config, storage, and source parsing.
- `config/example.yaml`: reproducible local configuration.
- `data/`: ignored local DuckDB databases and exports.

## Commands

```bash
uv sync --extra dev
uv run ccquant sync universe --size 100
uv run ccquant sync backfill --interval 1d
uv run ccquant sync backfill --interval 1h --top 10
uv run ccquant status
uv run pytest
uv run ruff check .
uv run mypy src
```

## Coding Style

Use Python 3.12+, frozen dataclasses for immutable records, and explicit type
annotations. Keep ingestion code deterministic and side-effect-light. Runtime
dependencies should stay small; add heavier modeling libraries behind optional
extras such as `forecast` or `notebook`.

## Testing

Tests use `pytest` and `pytest-asyncio`. Mock HTTP clients for source adapters;
do not require live API calls in unit tests. Name tests by behavior, for example
`test_fetch_binance_daily_parses_kline`.

## Data Discipline

Do not commit databases, exports, credentials, or notebook outputs. Store local
data under `data/` and make reproducibility flow through CLI commands and config.

