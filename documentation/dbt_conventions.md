# dbt Conventions

This project follows the [dbt Labs three-layer structure](https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview) adapted for a local DuckDB ELT pipeline.

## Ownership

| Schema | Owner | Purpose |
|---|---|---|
| `main` | Python (`src/ccquant`) | Raw landing tables — dbt never writes here |
| `main_staging` | dbt | Thin 1:1 views over raw sources |
| `main_intermediate` | dbt | Joins, dedup, business logic — not exposed to consumers |
| `main_marts` | dbt | Facts, dims, canonical panels |
| `main_signals` | dbt | Domain signal tables (on-chain, macro, wallet) |
| `main_events` | dbt | Curated events registry |

## Naming

| Prefix | Layer | Example |
|---|---|---|
| `stg_` | staging | `stg_ohlcv_daily` |
| `int_` | intermediate | `int_ohlcv_daily_deduped` |
| `fct_` | marts/signals facts | `fct_open_interest` |
| `dim_` | marts dimensions | `dim_assets` |
| `mart_` | wide consumer tables | `mart_signals_daily` |

## Rules

1. **Staging is thin** — cast, rename, dedupe keys; no joins between staging models.
2. **Intermediate holds logic** — source-priority dedup, USD normalization, event rollups.
3. **Marts never `source()`** — always `ref()` staging or intermediate models.
4. **Every model gets PK tests** — `unique` + `not_null` or `dbt_utils.unique_combination_of_columns`.
5. **High-volume facts are incremental** — `merge` strategy with 7-day tail window aligned to Python sync.

## Tags

Selective builds by domain:

```bash
uv run dbt build --select tag:market+ --project-dir dbt --profiles-dir dbt
uv run dbt build --select tag:wallet+ --project-dir dbt --profiles-dir dbt
uv run dbt build --select tag:social+ --project-dir dbt --profiles-dir dbt
uv run dbt build --select tag:ops+ --project-dir dbt --profiles-dir dbt
uv run dbt snapshot --project-dir dbt --profiles-dir dbt   # SCD2 snap_assets before build if needed
```

## Verify order

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run dbt deps --project-dir dbt --profiles-dir dbt
uv run dbt build --project-dir dbt --profiles-dir dbt
uv run dbt build --select package:dbt_project_evaluator --project-dir dbt --profiles-dir dbt
```

## When to add intermediate vs mart

- **Intermediate:** reusable join/dedup used by 2+ downstream models.
- **Mart:** final grain intended for notebooks, forecasting, or export.
