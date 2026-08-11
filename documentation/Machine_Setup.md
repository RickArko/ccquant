# New Machine / Mac Mini Setup

Credit-safe way to bring up ccquant on a fresh machine without burning paid
API credits or hammering rate limits.

**Prefer copy-first.** Re-pulling full history from APIs is slow and, for BID /
wallets, expensive or heavy. A DuckDB file copy is the supported bootstrap path.

See also: [`API_Pricing.md`](API_Pricing.md) for keys and tiers.

---

## Prerequisites (macOS)

```bash
# Homebrew: https://brew.sh
brew install uv git
git clone <your-ccquant-remote> ccquant
cd ccquant
cp .env.example .env   # then fill free keys (see below)
```

Recommended first install:

```bash
uv sync --extra dbt
# add --extra wallet only when you intend to enable wallet sync
```

Free keys to set in `.env` (own lines; no inline `#` comments):

| Key | Required for | Cost |
|---|---|---|
| `CG_DEMO_API_KEY` | Stable CoinGecko universe / OHLCV fallback | Free Demo |
| `FRED_API_KEY` | Macro sync | Free |
| `BITCOIN_IS_DATA_KEY` | BID MVRV/NUPL (optional) | Paid (~$12.50/yr) |
| `BID_CSV_PATH` | Local BID CSV instead of API | Paid download |

Leave `BITCOIN_IS_DATA_KEY` blank until you need valuation refresh.

---

## Path A — Copy-first (recommended)

### On the source machine

```bash
uv run ccquant db backup --dest data/backups --keep 10
```

Transfer:

- `data/backups/ccquant-YYYYMMDD-HHMMSS.duckdb` (or a checkpointed `data/ccquant.duckdb`)
- `.env` (consider blanking `BITCOIN_IS_DATA_KEY`)
- Optional: `data/mev/`, twitter inbox, BID CSV

### On the new machine

```bash
uv sync --extra dbt
uv run ccquant sync bootstrap --from-backup ~/Transfers/ccquant-YYYYMMDD-HHMMSS.duckdb --force-restore
uv run ccquant status
```

`sync bootstrap` prints a credit plan, restores the DB, then lean-tails free
domains. **BID and wallets are off by default.**

Preview without writing:

```bash
uv run ccquant sync bootstrap --from-backup PATH --dry-run
```

Restore only (no sync):

```bash
uv run ccquant db restore --source PATH --force
```

---

## Path B — Cold start (no backup)

Use only when you have no DuckDB to copy. This pulls **full daily history** once
(Binance → Coinbase → CoinGecko), which is free but slow.

```bash
uv sync --extra dbt
# .env: FRED_API_KEY + CG_DEMO_API_KEY; leave BID unset
uv run ccquant sync bootstrap --cold --dry-run
uv run ccquant sync bootstrap --cold
# optional tighter universe / delays:
uv run ccquant sync bootstrap --cold -c config/lean.yaml
```

Opt-ins (off by default):

```bash
uv run ccquant sync bootstrap --cold --allow-bid      # paid BID API / CSV
uv run ccquant sync bootstrap --cold --with-wallets   # SolArchive / BQ / RPC
```

`--cold` refuses if daily `backfill_complete` is already true for all active
assets (use `--from-backup` for machine moves, or `--force-cold` to override).

---

## After bootstrap — routine updates

Once history is complete, prefer:

```bash
uv run ccquant sync all --no-onchain     # avoid BID re-pull
# or selective:
uv run ccquant sync backfill --interval 1d --tail
uv run ccquant sync macro
uv run ccquant status
```

Refresh on-chain valuation only when needed:

```bash
uv run ccquant sync onchain --force      # ignores freshness; BID if keyed
# or: uv run ccquant sync bootstrap --from-backup PATH --allow-bid ...
```

Notes:

- `sync onchain` skips blockchain.info / BID when data is already fresh (max date
  ≥ yesterday) unless `--force`.
- `BID_CSV_PATH` is loaded before the BID API when present (and skips the API
  when the CSV load succeeds, unless `--force`).
- `sync all` still enables BID when keyed and wallets by default — use flags /
  blank keys if you want the lean posture for routine runs.

---

## What burns credits / limits

| Risk | When | Mitigation |
|---|---|---|
| **BID (paid)** | `BITCOIN_IS_DATA_KEY` set + onchain sync | Bootstrap defaults BID off; leave key blank; use CSV / freshness skip |
| **CoinGecko limits** | Universe + daily fallback | Set `CG_DEMO_API_KEY` (wired into sync) |
| **Wallet HEAVY_IO** | First wallet history / public RPC | Bootstrap skips wallets unless `--with-wallets` |
| **Empty-DB `sync all`** | Daily full history × universe | Prefer backup restore or `sync bootstrap --cold` |

Do **not** use `sync backfill --force` on a fresh machine unless you intend to
re-download full history.
