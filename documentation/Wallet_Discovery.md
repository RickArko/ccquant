# Wallet Discovery Playbook

Methods for curating famous, insider, and smart-money wallets for ccquant
wallet intelligence. All v1 sources are **free**; paid APIs (Nansen, Arkham,
Helius) are optional upgrades documented in [`API_Pricing.md`](API_Pricing.md).

## Tier 1 — Performance-based smart money

| Method | Source | Signal |
|---|---|---|
| PnL-ranked wallets | Birdeye / DEXScreener Top Traders, GMGN leaderboard | Win rate, realized PnL |
| Flipside labels | `solana.core.dim_labels` + swap joins | CEX, deployers, treasuries |
| Heuristic scorer | `ccquant wallet discover` | Custom `smart_money` label |

**Heuristic filters** (community consensus):

- Win rate > 35–50% over 100+ closed trades
- Median hold time > 2 hours (filters bots)
- Early buyer rank: top 50 within 10 min of launch
- Repeat deployer with prior 10x token

## Tier 2 — Identity resolution (KOL / insider)

| Method | Channel | Steps |
|---|---|---|
| `.sol` SNS | Twitter/X | Search `from:@handle .sol` → `ccquant wallet resolve-sns` |
| Giveaway backtrack | Twitter, Dune | Partial address + amount/date filter on Solscan |
| Holder screenshot | Twitter/Reddit | Match exact token balance on holder list |
| Swap reverse-engineer | Public trade screenshots | Filter transfers by amount + timestamp |
| Dev/team wallets | Solscan, RugCheck | Token creator / mint authority → funding cluster |
| Bridge linkage | Flipside Wormhole tags | ETH↔SOL address pairs |

## Tier 3 — Cluster / cabal detection

Implemented in dbt (`fct_wallet_cabal_events`):

- **Shared funder** — multiple watched wallets funded from same source
- **Co-buy window** — N wallets buy same mint within 5 minutes
- **Deployer cluster** — deployer + funded buyers accumulate pre-social

## Seed wallet curation

1. Start from [`config/seeds/wallet_registry_seed.csv`](../config/seeds/wallet_registry_seed.csv)
   (50+ labeled addresses across Solana and Arbitrum).
2. Add local overrides in `data/seeds/wallet_registry_seed.csv` (gitignored).
3. Run `ccquant wallet discover --chain solana --top 20` to grow registry from
   Flipside labels (cached weekly).

## Open extract smoke test

Before scaling historical backfill:

1. Pick 3 seed wallets (KOL, deployer, CEX hot wallet).
2. Download one SolArchive day partition **or** run a bounded 7-day extract.
3. Validate with `ccquant wallet import-extract --source solarchive --date YYYY-MM-DD`.
4. Confirm idempotent re-run: row counts stable, no duplicates.

```bash
# Pick a date that exists on HuggingFace (CDN may 404; CLI falls back automatically)
uv run ccquant wallet import-extract --source solarchive --date 2025-12-05
uv run ccquant sync wallets --full
```

## Reddit / forum sources (manual monitoring)

- r/solana, r/CryptoCurrency wallet threads
- [Flipside label submissions](https://science.flipsidecrypto.xyz/add-a-label)
- Dune dashboards (e.g. KOL wallet discovery queries)
- Solana Stack Exchange historical-data discussions

## What not to rely on in v1

| Source | Reason |
|---|---|
| Nansen / Arkham API | Paid ($100–800+/mo); use public dashboards for manual seeds only |
| GMGN API | Closed; leaderboard UI export only |
| Lookonchain | Twitter alerts; optional monitor, not primary ingest |

## CLI reference

Options are separate flags — do not paste bracket notation like `[--full|--no-tail]`.

```bash
uv run ccquant sync wallets
uv run ccquant sync wallets --full
uv run ccquant sync wallets --no-tail
uv run ccquant wallet discover --chain solana --top 20
uv run ccquant wallet import-extract --source solarchive --date 2025-12-05
uv run ccquant wallet resolve-sns mitch.sol
uv run ccquant wallet match-holder --mint TOKEN_MINT --amount 49995519
uv run ccquant wallet alerts --since 1
```

## Backup and rollback

```bash
# Before large extracts or schema changes
uv run ccquant db backup --dest data/backups --keep 10

# Restore (stop any open connections first)
cp data/backups/ccquant-YYYYMMDD-HHMMSS.duckdb data/ccquant.duckdb

# Rebuild dbt wallet models after restore
uv run dbt run --select stg_wallet_* fct_wallet_* --project-dir dbt --profiles-dir dbt
uv run dbt run --select mart_signals_daily --full-refresh --project-dir dbt --profiles-dir dbt
```
