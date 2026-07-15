"""Insert minimal rows into DuckDB for dbt CI integration tests."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb


def seed_dbt_ci_fixture(database: str | Path | None = None) -> Path:
    """Populate an initialized DuckDB file with fixture rows for dbt build/test.

    Expects MarketStore.init_schema() to have already created all main tables.
    """
    db_path = Path(database or os.environ.get("CCQUANT_DB", "data/ccquant.duckdb"))

    now = datetime.now(tz=UTC).replace(tzinfo=None)
    today = date.today()
    start = today - timedelta(days=29)

    tables = [
        "assets",
        "ohlcv_daily",
        "ohlcv_hourly",
        "sync_state",
        "onchain_series",
        "onchain_sync_state",
        "macro_series",
        "macro_sync_state",
        "open_interest",
        "tweet_signals_daily",
        "wallet_registry",
        "wallet_transfers",
        "wallet_positions_daily",
        "wallet_signals_daily",
        "wallet_alerts",
        "wallet_sync_state",
        "wallet_identities",
        "wallet_identity_links",
        "twitter_accounts",
        "tweets",
        "tweet_entities",
        "tweet_alerts",
    ]

    with duckdb.connect(str(db_path)) as conn:
        for table in tables:
            conn.execute(f"delete from main.{table}")

        for rank, symbol in enumerate(["BTC", "ETH", "SOL"], start=1):
            conn.execute(
                """
                insert into main.assets (
                  rank, symbol, coingecko_id, binance_pair, coinbase_product_id,
                  active, as_of_date
                ) values (?, ?, ?, ?, ?, true, ?)
                """,
                [rank, symbol, symbol.lower(), f"{symbol}USDT", f"{symbol}-USD", today],
            )
            conn.execute(
                """
                insert into main.sync_state (
                  symbol, interval, backfill_complete, earliest_at, latest_at,
                  last_refresh_at
                ) values (?, '1d', true, ?, ?, ?)
                """,
                [
                    symbol,
                    start.isoformat(),
                    today.isoformat(),
                    now,
                ],
            )

        day = start
        while day <= today:
            for symbol, close in [("BTC", 50000.0), ("ETH", 3000.0), ("SOL", 100.0)]:
                conn.execute(
                    """
                    insert into main.ohlcv_daily (
                      symbol, date, open, high, low, close, volume, source
                    ) values (?, ?, ?, ?, ?, ?, ?, 'binance')
                    """,
                    [symbol, day, close, close * 1.01, close * 0.99, close, 1000.0],
                )
                conn.execute(
                    """
                    insert into main.open_interest (
                      symbol, timestamp, open_interest, exchange, unit, interval
                    ) values (?, ?, ?, 'binance', 'usd_notional', '1d')
                    """,
                    [symbol, datetime.combine(day, datetime.min.time()), 1_000_000.0],
                )
            conn.execute(
                """
                insert into main.onchain_series (metric, date, value, source) values
                ('mvrv', ?, 1.5, 'coinmetrics'),
                ('nupl', ?, 0.2, 'coinmetrics')
                """,
                [day, day],
            )
            conn.execute(
                """
                insert into main.macro_series (series_id, date, value, source) values
                ('M2SL', ?, 20000.0, 'fred'),
                ('DGS10', ?, 4.0, 'fred')
                """,
                [day, day],
            )
            day += timedelta(days=1)

        conn.execute(
            """
            insert into main.onchain_sync_state (
              metric, source, latest_at, last_refresh_at
            ) values ('mvrv', 'coinmetrics', ?, ?)
            """,
            [now, now],
        )
        conn.execute(
            """
            insert into main.macro_sync_state (
              series_id, source, latest_at, last_refresh_at
            ) values ('M2SL', 'fred', ?, ?)
            """,
            [now, now],
        )

        conn.execute(
            """
            insert into main.tweet_signals_daily (
              date, symbol, mention_count, kol_mention_count,
              bullish_keyword_count, bearish_keyword_count, unique_accounts
            ) values
            (?, 'BTC', 5, 2, 3, 1, 4),
            (?, 'ETH', 2, 1, 1, 0, 2)
            """,
            [today, today],
        )
        conn.execute(
            """
            insert into main.wallet_registry (
              address, chain, label, entity_type, confidence, source,
              discovered_at, active, metadata_json
            ) values
            (
              'abc123', 'solana', 'test_wallet', 'smart_money', 0.9, 'manual',
              ?, true, '{}'
            ),
            (
              'bc1q0354ypeak66322j0zr0ysm4wd9qkx2x66e4q0', 'bitcoin',
              'Strategy Treasury', 'insider', 0.9, 'manual', ?, true, '{}'
            ),
            (
              '34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi', 'bitcoin',
              'Binance Cold', 'exchange', 0.95, 'manual', ?, true, '{}'
            )
            """,
            [now, now, now],
        )
        conn.execute(
            """
            insert into main.wallet_identities (
              identity_id, display_name, category, description, source_url, active
            ) values
            ('strategy', 'MicroStrategy', 'corporate', '', '', true),
            ('binance', 'Binance', 'exchange', '', '', true)
            """
        )
        conn.execute(
            """
            insert into main.wallet_identity_links (
              address, chain, identity_id, link_type, confidence, source, linked_at
            ) values
            (
              'bc1q0354ypeak66322j0zr0ysm4wd9qkx2x66e4q0', 'bitcoin', 'strategy',
              'owns', 0.9, 'manual', ?
            ),
            (
              'bc1qjasf9z3h7l3jkaware86a4s4ut9t928cerovd', 'bitcoin', 'strategy',
              'owns', 0.85, 'manual', ?
            ),
            (
              '34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi', 'bitcoin', 'binance',
              'operates', 0.95, 'manual', ?
            )
            """,
            [now, now, now],
        )
        conn.execute(
            """
            insert into main.wallet_transfers (
              chain, tx_hash, transfer_index, block_time, from_address, to_address,
              asset_mint_or_contract, asset_symbol, amount, amount_usd, direction,
              program_or_method, source
            ) values
            (
              'bitcoin', 'btc_tx_1', 0, ?, '34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi',
              'bc1q0354ypeak66322j0zr0ysm4wd9qkx2x66e4q0',
              'btc', 'BTC', 50.0, 2500000.0,
              'inflow', 'p2pkh', 'fixture'
            ),
            (
              'bitcoin', 'btc_tx_2', 0, ?, 'bc1q0354ypeak66322j0zr0ysm4wd9qkx2x66e4q0',
              '34xp4vRoCG5Jh1B5fszvzu5uBmM2a5jSNi', 'btc', 'BTC', 25.0, 1250000.0,
              'outflow', 'p2pkh', 'fixture'
            )
            """,
            [now, now],
        )

    return db_path


if __name__ == "__main__":
    from ccquant.config import load_config
    from ccquant.storage import MarketStore

    cfg = load_config()
    store = MarketStore(cfg.database)
    store.close()
    path = seed_dbt_ci_fixture(cfg.database)
    print(f"Seeded dbt CI fixture: {path}")
