# API Pricing & Data Source Decision Guide

All data sources used across `ccquant` — OHLCV sync, the `Macro.ipynb` /
`btc.ipynb` notebooks, and the `OnChain_BTC.ipynb` on-chain notebook — with
pricing tiers, free-tier limits, key setup, and a recommended v1 stack.

> **Last verified:** July 2026. Prices change — verify at vendor sites before
> subscribing. All prices exclude VAT unless noted.

---

## Quick decision matrix

| Source | Role | Free? | Key required? | V1 recommendation |
|---|---|---|---|---|
| **Binance** | OHLCV candles (price/volume) | Free, keyless | No | **Use** (ccquant sync) |
| **Coinbase** | OHLCV candles (fallback) | Free, keyless | No | **Use** (ccquant sync) |
| **CoinGecko** | Universe ranking + OHLCV fallback | Free Demo (10k calls/mo) | Demo key (free) | **Use** (ccquant sync) |
| **FRED** | Macro indicators (M2, rates, Fed BS) | Free | `FRED_API_KEY` | **Use** (Macro / btc notebooks) |
| **blockchain.info** | On-chain fundamentals (hashrate, miner rev, etc.) | Free, keyless | No | **Use** (OnChain notebook) |
| **bitcoinisdata.com** | On-chain valuation (MVRV, NUPL, realized price) | Free sample (~15d) | `BITCOIN_IS_DATA_KEY` (paid) | **Use** (renew sub for full history) |
| **Glassnode** | Premium valuation (SOPR, RHODL, exchange balance) | Display-only | `GLASSNODE_API_KEY` (Pro tier) | Optional (synthetic fallback otherwise) |
| CryptoQuant | Exchange flows, SOPR, MVRV | Free charts, paid API | `CRYPTOQUANT_API_KEY` | Future v2 (exchange flows) |
| **X / Twitter** | KOL/trader tweet tracking | Import-only v1 ($0) | `X_API_BEARER_TOKEN` (Phase 2+) | **Use** CSV/JSONL import |
| Bitcoin Magazine Pro | Puell, MVRV Z-Score, RHODL | Display-only | No API | Reference only (no programmatic access) |

---

## V1 recommended stack (zero or ~$12.50/yr)

```
OHLCV:        Binance → Coinbase → CoinGecko (ccquant sync, keyless/free)
Macro:        FRED (free key)
On-chain:     blockchain.info (keyless) + bitcoinisdata.com (~25k sats/yr)
Valuation:    bitcoinisdata.com MVRV/NUPL/realized_price (real) +
              SOPR/RHODL/exchange_balance (synthetic fallback)
```

**Total cost:** $0 if you skip bitcoinisdata.com; ~$12.50/yr if you subscribe.
**API keys needed:** `FRED_API_KEY` (free), `BITCOIN_IS_DATA_KEY` (with BID sub).
**Keys NOT needed for v1:** Glassnode, CryptoQuant, CoinGecko paid tiers.

---

## Source details

### Binance — OHLCV candles

Used by `ccquant sync backfill` as the primary OHLCV source.

| Tier | Price | Rate limit | History |
|---|---|---|---|
| Public (no key) | **Free** | 1200 weight/min | Full |

- **Key:** None required for public market data.
- **Endpoints:** `/api/v3/klines` (daily/hourly candles).
- **Notes:** Weight-based rate limiting; klines cost 1-2 weight each. No key
  needed. Already wired in `src/ccquant/sources.py`.

---

### Coinbase — OHLCV candles (fallback)

Used by `ccquant sync backfill` as the secondary OHLCV source.

| Tier | Price | Rate limit | History |
|---|---|---|---|
| Public (no key) | **Free** | Generous | Full |

- **Key:** None required for public market data.
- **Endpoints:** `/api/v3/brokerage/market/products/{id}/candles`.
- **Notes:** Already wired in `src/ccquant/sources.py`.

---

### CoinGecko — Universe ranking + OHLCV fallback

Used by `ccquant sync universe` to fetch the top-cap universe and as the final
OHLCV fallback (daily only).

| Tier | Price | Rate limit | History | Key |
|---|---|---|---|---|
| **Demo (free)** | $0 | 30 calls/min, 10k calls/mo | 365 days | `x_cg_demo_api_key` (free) |
| Analyst | $129/mo | 250 calls/min, 100k credits/mo | 2 years | `x-cg-pro-api-key` |
| Lite | ~$250/mo | 500 calls/min, 500k credits/mo | 10 years | `x-cg-pro-api-key` |
| Pro | $499/mo | 2500 calls/min | 10 years | `x-cg-pro-api-key` |
| Enterprise | Custom | Custom | Full | Custom |

- **Key setup (free Demo):**
  1. Sign up at [coingecko.com](https://www.coingecko.com)
  2. Developer Dashboard → Create API key (free Demo)
  3. Set in `.env`: `CG_DEMO_API_KEY=your_key`
- **Notes:** The public API without a Demo key is 5-15 calls/min (unstable).
  The free Demo key gives a stable 30 calls/min. Already wired in
  `src/ccquant/sources.py` (currently keyless; add Demo key for stability).
- **ccquant usage:** `sync universe` fetches top-100 by market cap (1-2 calls);
  daily backfill fallback (~180-day chunks per asset). 100 assets × 2 calls ≈
  200 calls — well within the 10k/mo free cap.

---

### FRED — Federal Reserve macro indicators

Used by `Macro.ipynb` and `btc.ipynb` for M2, Fed balance sheet, Treasury
yields, DXY, VIX, breakeven inflation, Fed funds rate.

| Tier | Price | Rate limit | History |
|---|---|---|---|
| **Free** | $0 | 120 calls/min | Full (back to 1910s) |

- **Key setup:**
  1. Register at [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)
  2. Set in `.env`: `FRED_API_KEY=your_key`
- **Series used:** `M2SL`, `WALCL`, `DGS10`, `DGS2`, `T10YIE`, `FEDFUNDS`,
  `DTWEXBGS`, `VIXCLS`
- **Notes:** Completely free, generous limits. Without the key, notebooks
  degrade to synthetic macro series.

---

### blockchain.info — on-chain fundamentals (keyless)

Used by `OnChain_BTC.ipynb` for hashrate, difficulty, miner revenue, fees,
active addresses, tx count, transfer volume, market cap, supply, cost-per-tx.

| Tier | Price | Rate limit | History |
|---|---|---|---|
| **Free (keyless)** | $0 | No published limit (~60 req/min informal) | Full (2009+) |

- **Key:** None.
- **Endpoint:** `https://api.blockchain.info/charts/{chart}?timespan=all&format=json`
- **Response:** `{"values": [{"x": unix_ts, "y": value}, ...]}`
- **Rate limiting:** The notebook uses 1s spacing between requests, 12h
  staleness gate, idempotent DuckDB upserts. Don't go below ~0.5s spacing.
- **Charts used (10):** `hash-rate`, `difficulty`, `miners-revenue`,
  `transaction-fees-usd`, `n-unique-addresses`, `n-transactions`,
  `estimated-transaction-volume-usd`, `market-cap`, `total-bitcoins`,
  `cost-per-transaction-percent`

---

### bitcoinisdata.com — on-chain valuation (MVRV, NUPL, realized price)

Used by `OnChain_BTC.ipynb` for real MVRV, realized price, and NUPL. Cheapest
source of real on-chain valuation data.

| Tier | Price | Key? | History | Access |
|---|---|---|---|---|
| **Free sample** | $0 | None | ~15 days (recent) | CSV download (keyless) |
| **30 days** | 3,000 sats (~$1.50) | `BITCOIN_IS_DATA_KEY` | 30 days | API + CSV |
| **90 days** | 8,000 sats (~$4.00) | `BITCOIN_IS_DATA_KEY` | 90 days | API + CSV |
| **360 days** | 25,000 sats (~$12.50) | `BITCOIN_IS_DATA_KEY` | 1 year | API + CSV |

- **Payment:** BTC via Lightning Network (no credit card).
- **Key setup:**
  1. Subscribe at [bitcoinisdata.com/accounts/buy_subscription](https://bitcoinisdata.com/accounts/buy_subscription/)
  2. Pay the Lightning invoice
  3. Find your API key in My Account
  4. Set in `.env`: `BITCOIN_IS_DATA_KEY=your_key`
- **API endpoint:**
  `https://bitcoinisdata.com/api/get_data?api_key=KEY&start_block=N&columns=date,total_mvrv,total_realized_price,total_nupl&format=json`
- **CSV download:** Alternative to API — download from the Download page and
  set `BID_CSV_PATH` in `.env` to the file path.
- **Columns mapped:**

  | BID column | ccquant metric | What |
  |---|---|---|
  | `total_mvrv` | `mvrv` | Market-value to realized-value ratio |
  | `total_realized_price` | `realized_price` | Aggregate on-chain cost basis |
  | `total_nupl` | `nupl` | Net unrealized profit/loss |

- **Not available from BID:** SOPR, RHODL, exchange balance (need Glassnode).
- **Notes:** Data is per-block; the notebook aggregates to daily (last value
  per date). The free sample (~15 days) is too short for backtesting — the
  notebook's coverage check falls back to synthetic if BID data spans <50% of
  the weekly spine. **Subscribe for at least 360 days for full-history real
  valuation signals.**
- **Subscription expiry:** The API returns `"Hello {username}, subscription
  EXPIRED."` when the subscription lapses. The notebook detects this and falls
  back to the free sample / synthetic.

---

### Glassnode — premium valuation indicators

Used by `OnChain_BTC.ipynb` (optional) for SOPR, RHODL, exchange balance, and
as a fallback for MVRV/NUPL/realized price if bitcoinisdata.com is unavailable.

| Tier | Price | API? | History | Key |
|---|---|---|---|---|
| **Discover** | Free | No (display-only) | — | — |
| **Advanced** | $49/mo (annual) / $99/mo (monthly) | Light API (14d, 50 calls/day) | 14 days | `GLASSNODE_API_KEY` |
| **Professional** | $799–999/mo + Data Credits add-on | Full API | 15+ years | `GLASSNODE_API_KEY` |
| **Institutional** | Custom | Full + redistribution | Full | Custom |

- **Data Credits:** Metered add-on on Professional. 1 credit = 1 BTC API call,
  2 credits = 1 altcoin call. Per-credit rate not published.
- **Key setup:**
  1. Subscribe to Professional at [studio.glassnode.com/pricing](https://studio.glassnode.com/pricing)
  2. Add the Data Credits add-on
  3. Generate API key in Studio → Settings → API
  4. Set in `.env`: `GLASSNODE_API_KEY=your_key`
- **Endpoints used:**
  - `market/mvrv` → MVRV
  - `indicators/sopr` → SOPR
  - `indicators/nupl` → NUPL
  - `market/price-realized-usd` → realized price
  - `indicators/rhodl-ratio` → RHODL
  - `transactions/balance-exchanges` → exchange balance
- **Notes:** The Advanced tier's Light API (14-day history, 50 calls/day) is
  insufficient for backtesting. You need Professional + Data Credits for
  research-grade historical data. Without a key, all six metrics use synthetic
  fallback. The notebook throttles at 6s between Glassnode requests.

---

### CryptoQuant — exchange flows, SOPR, MVRV (evaluated, not yet integrated)

Alternative to Glassnode for exchange flow data. Strong on exchange
inflow/outflow tracking across 20+ exchanges.

| Tier | Price | API? | History | Key |
|---|---|---|---|---|
| **Basic (Free)** | $0 | No (charts only, 7d history) | 7 days | — |
| **Advanced** | $29/mo | Yes (1,000 calls/mo) | 2 years | `CRYPTOQUANT_API_KEY` |
| **Professional** | $99/mo | Yes (10,000 calls/mo) | Full | `CRYPTOQUANT_API_KEY` |
| **Premium** | $799/mo | Yes (unlimited) | Full | `CRYPTOQUANT_API_KEY` |

- **Key setup:**
  1. Subscribe at [cryptoquant.com/pricing](https://cryptoquant.com/pricing)
  2. Find access token in Settings → API
  3. Set in `.env`: `CRYPTOQUANT_API_KEY=your_key`
- **API:** `https://api.cryptoquant.com/v1/` with `Authorization: Bearer {token}`
- **Metrics of interest:** exchange reserves, inflow/outflow, net flow, SOPR,
  MVRV, Puell Multiple, miner flows, funding rates.
- **V2 plan:** Wire CryptoQuant for exchange flow data (inflow/outflow/netflow)
  that neither blockchain.info nor bitcoinisdata.com provides. The Advanced
  tier ($29/mo, 1000 calls/mo) is sufficient for daily refresh of ~5 exchange
  flow metrics (150 calls/mo).

---

### Bitcoin Magazine Pro / LookIntoBitcoin — cycle indicators (reference only)

Charts for Puell Multiple, MVRV Z-Score, RHODL, Reserve Risk, SOPR.

| Tier | Price | Data access | API? |
|---|---|---|---|
| **Free** | $0 | Display-only charts | No |
| **Pro** | $1,188/yr | CSV downloads, alerts, TradingView indicators | No API |

- **Notes:** No programmatic API. CSV downloads require the Pro subscription.
  Not integrated into ccquant. Use as a visual reference / cross-check.

---

### Wallet intelligence data sources (v1 — $0 stack)

Used by `ccquant sync wallets` and `Wallet_SOL.ipynb`.

| Source | Role | Free? | Key? | V1 recommendation |
|---|---|---|---|---|
| **SolArchive** | Solana historical Parquet partitions | Free (CC-BY-4.0) | No | **Use** for bounded history backfill |
| **BigQuery public** | Solana + Arbitrum SQL extracts | Free (1 TB/mo) | GCP creds optional | **Use** with `uv sync --extra wallet` |
| **Flipside** | Wallet labels (`dim_labels`) | Free tier | `FLIPSIDE_API_KEY` | **Use** for discovery |
| **Solana public RPC** | Tail refresh (`getSignaturesForAddress`) | Free | No | **Use** (rate-limited, ≤50 wallets) |
| **camp** | Arbitrum tail REST API | Free | No | **Use** (rolling ~30d window) |
| **Etherscan** | ETH/Arbitrum ERC-20 tail | Free (100k calls/day) | `ETHERSCAN_API_KEY` | Optional |
| **Helius** | Solana archival `getTransactionsForAddress` | Paid ($49+/mo) | API key | Upgrade path |
| **Nansen / Arkham** | Institutional smart-money labels | Paid ($100–800+/mo) | API key | Manual seeding only |

**Open extract testing protocol:**

1. Load seed registry from `config/seeds/wallet_registry_seed.csv`
2. Import one partition: `uv run ccquant wallet import-extract --source solarchive --date YYYY-MM-DD`
3. Or local parquet: `uv run ccquant wallet import-extract --source solarchive --parquet PATH`
4. Re-run to verify idempotent upserts before scaling `extract_days`

**Rollback:** `uv run ccquant db backup` before large extracts; restore file copy to roll back.

---

### X / Twitter — crypto KOL/trader tweet tracking

Used by `ccquant sync tweets` for social signal enrichment. **v1 is import-only**
($0) — drop CSV/JSONL exports into `data/twitter/inbox/`. See
[`Twitter_Import.md`](Twitter_Import.md).

| Tier | Price | Use case | V1 recommendation |
|---|---|---|---|
| **CSV/JSONL import** | **$0** | Manual exports, saved archives | **Use** (primary v1) |
| Pay-per-use API | ~$0.005/post read | Live 15-min tail for ≤50 accounts | Phase 2+ (~$2–5/mo) |
| Basic (legacy) | $200/mo | Fixed monthly cap | Not for new signups |
| Enterprise | $42k+/mo | Full firehose | Not needed |

- **Key setup (Phase 2+ only):**
  1. Register at [developer.x.com](https://developer.x.com)
  2. Create app with pay-per-use billing + spending cap
  3. Set in `.env`: `X_API_BEARER_TOKEN=your_token`
- **v1 workflow:** Export tweets externally → `data/twitter/inbox/` →
  `uv run ccquant sync tweets`
- **Notes:** X discontinued meaningful free read access (Feb 2026). ccquant
  does not ship scrapers; external export tools only. Daily deduplication on
  the API means tail refresh cost is lower than raw per-post pricing.

---

## .env key reference

All keys are loaded via `python-dotenv` from the project root `.env` file.
Copy `.env.example` to `.env` and fill in your keys.

```bash
# .env — copy from .env.example and fill in
cp .env.example .env
```

| Variable | Source | Free? | Required for |
|---|---|---|---|
| `FRED_API_KEY` | FRED | Yes | Macro.ipynb, btc.ipynb (real macro data) |
| `CG_DEMO_API_KEY` | CoinGecko | Yes | ccquant sync universe (stable rate limit) |
| `BITCOIN_IS_DATA_KEY` | bitcoinisdata.com | Paid (~$12.50/yr) | OnChain_BTC.ipynb (real MVRV/NUPL) |
| `BID_CSV_PATH` | bitcoinisdata.com | — | OnChain_BTC.ipynb (CSV download path) |
| `GLASSNODE_API_KEY` | Glassnode | Paid ($799+/mo) | OnChain_BTC.ipynb (SOPR/RHODL/exch bal) |
| `CRYPTOQUANT_API_KEY` | CryptoQuant | Paid ($29+/mo) | Future v2 (exchange flows) |
| `FLIPSIDE_API_KEY` | Flipside | Free tier | `ccquant wallet discover` |
| `ETHERSCAN_API_KEY` | Etherscan | Free | Optional Arbitrum/ETH tail |
| `TELEGRAM_BOT_TOKEN` | Telegram | Free | Optional wallet/tweet alerts |
| `TELEGRAM_CHAT_ID` | Telegram | Free | Optional alert destination chat |
| `X_API_BEARER_TOKEN` | X / Twitter | Pay-per-use | Phase 2+ live tweet tail (not v1) |
| `CCQUANT_DB` | — | — | Override OHLCV DuckDB path |
| `CCQUANT_ONCHAIN_DB` | — | — | Override on-chain DuckDB path |

### Keys NOT set by default (optional)

- `GLASSNODE_API_KEY` — Professional tier is expensive; synthetic fallback works
- `CRYPTOQUANT_API_KEY` — not yet integrated; planned for v2

---

## Rate-limiting & terms compliance

Each source in ccquant is accessed responsibly to avoid IP bans and respect
terms of service:

| Source | Strategy |
|---|---|
| Binance / Coinbase | Keyless public endpoints; ccquant sync uses `request_delay_seconds` (default 0.25s) |
| CoinGecko | 0.25s delay; 180-day chunks; 429-retry with 60s backoff |
| FRED | Sequential, one request per series per run |
| blockchain.info | 1s spacing, 12h staleness gate, sequential, 429-retry |
| bitcoinisdata.com | API: sequential, cached in DuckDB; CSV: one-time download |
| Glassnode | 6s spacing, 12h staleness gate, 50 calls/day cap (Light API) |
| Solana public RPC | 1 req/s per wallet; max 50 wallets in tail config |
| Flipside | Cache labels in DuckDB; refresh weekly not hourly |
| SolArchive | Download single-day partitions only; filter to seed wallets |
| Twitter import | Idempotent on `tweet_id`; inbox files archived after import |

## Tweet tracking rollback

```bash
uv run ccquant db backup
# restore: cp data/backups/ccquant-YYYYMMDD-HHMMSS.duckdb data/ccquant.duckdb
uv run dbt run --select tag:twitter --full-refresh --project-dir dbt --profiles-dir dbt
```

All on-chain data is cached in a local DuckDB store (`data/onchain.duckdb`) with
incremental refresh — re-running the notebook only hits the network for stale
metrics (older than 12 hours). This keeps repeated runs fast and respectful.
