# Twitter / X Import Guide

Import-only tweet tracking for ccquant — **$0/month**, no live X API in v1.
Drop CSV or JSONL exports into the inbox; `ccquant sync tweets` normalizes,
dedupes, enriches, and archives files.

## Quick start

```bash
# 1. Copy exports into the inbox
cp my_export.csv data/twitter/inbox/

# 2. Import + enrich
uv run ccquant sync tweets

# 3. Re-run is idempotent (same tweet_id → no duplicates)
uv run ccquant sync tweets
```

Seed accounts live in [`config/seeds/twitter_accounts_seed.csv`](../config/seeds/twitter_accounts_seed.csv).
Override locally via `data/seeds/twitter_accounts_seed.csv` (gitignored).

## Inbox workflow

| Directory | Purpose |
|---|---|
| `data/twitter/inbox/` | Drop pending CSV/JSONL files here |
| `data/twitter/archive/` | Successfully imported files moved here |

Configure paths in `config/example.yaml` under `twitter_tracking.import`.

## CSV format (canonical)

**Required columns:**

| Column | Description |
|---|---|
| `tweet_id` | Stable dedup key (string) |
| `handle` | Lowercase username without `@` |
| `posted_at` | ISO 8601 UTC (`2025-06-01T12:00:00Z`) |
| `text` | Full tweet body |

**Optional columns:**

| Column | Default |
|---|---|
| `like_count`, `retweet_count`, `reply_count` | `0` |
| `is_retweet`, `is_reply` | `false` |
| `lang` | empty |
| `reply_to_tweet_id`, `conversation_id` | empty |
| `user_id` | empty |

**Example:**

```csv
tweet_id,handle,posted_at,text,like_count,retweet_count,is_retweet,is_reply
1234567890,ansem,2025-06-01T12:00:00Z,Long $SOL here looking strong,42,10,false,false
```

## JSONL format

One tweet object per line. Common field aliases are mapped automatically:

| Export field | Maps to |
|---|---|
| `id` | `tweet_id` |
| `created_at` | `posted_at` |
| `author.username` / `user.screen_name` | `handle` |
| `full_text` / `content` | `text` |
| `favorite_count` / `like_count` | `like_count` |
| `retweet_count` | `retweet_count` |

**Example:**

```jsonl
{"id":"123","created_at":"2025-06-01T12:00:00Z","author":{"username":"ansem"},"text":"Long $SOL"}
```

## External export tooling

Use any tool that produces CSV/JSONL with the columns above. ccquant does
**not** ship scrapers (ToS risk). Recommended external options:

| Tool | Output | Notes |
|---|---|---|
| Browser bookmarklet / extension | CSV | Manual per-account export |
| Tweet archiver scripts | JSONL | Run locally; drop output in inbox |
| Saved alert-bot screenshots | Manual CSV | Transcribe into canonical CSV |
| X API (Phase 2+) | JSONL | ~$2–5/mo pay-per-use; see `API_Pricing.md` |

## Conflict handling

`twitter_tracking.import.on_conflict`:

- `skip` (default) — existing `tweet_id` rows are not updated
- `update_metrics` — refresh `like_count`, `retweet_count`, `reply_count`

## Enrichment

On import, ccquant extracts:

- **Cashtags** — `$SOL`, `$BTC` → `tweet_entities` (`cashtag` / `symbol`)
- **Solana addresses** — base58 32–44 chars
- **ETH addresses** — `0x` + 40 hex
- **`.sol` domains** — SNS names for optional wallet resolution
- **Keyword sentiment** — bullish/bearish word counts (no ML)

Unknown cashtags are stored but only mapped symbols in the active universe
join to `tweet_signals_daily`.

## Account discovery

Handles in imports that are not in the seed file are auto-added as
`source=import_discovered`, `active=false`. Review and promote:

```bash
uv run ccquant twitter review
uv run ccquant twitter accounts promote somehandle
```

## CLI reference

```bash
uv run ccquant sync tweets                    # inbox import + enrich + signals
uv run ccquant twitter import PATH     # single file import
uv run ccquant twitter accounts list          # active watchlist
uv run ccquant twitter accounts add HANDLE    # add account manually
uv run ccquant twitter review                 # pending discovered handles
uv run ccquant twitter alerts --since 24      # batch alerts (hours)
```

## Rollback

```bash
uv run ccquant db backup
# restore backup file over data/ccquant.duckdb
uv run dbt run --select tag:twitter --full-refresh
```

## Upgrade path (Phase 2+)

When budget allows, enable live tail via X API pay-per-use (~$0.005/post).
Set `X_API_BEARER_TOKEN` in `.env` and `twitter_tracking.api.enabled: true`
(future). Until then, schedule regular inbox drops (daily/weekly).
