from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import duckdb

from ccquant.models import (
    Asset,
    DailyOhlcv,
    HourlyOhlcv,
    MacroPoint,
    OnchainPoint,
    OpenInterest,
    SyncState,
    Tweet,
    TweetAlert,
    TweetEntity,
    TweetSignalDaily,
    TweetSyncState,
    TwitterAccount,
    WalletAlert,
    WalletIdentity,
    WalletIdentityLink,
    WalletPositionDaily,
    WalletRegistryEntry,
    WalletSyncState,
    WalletTransfer,
)

Interval = Literal["1d", "1h"]


class MarketStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.path))
        self.init_schema()

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        self._conn.execute(
            """
            create table if not exists assets (
              rank integer not null,
              symbol varchar not null,
              coingecko_id varchar not null,
              binance_pair varchar,
              coinbase_product_id varchar,
              active boolean not null default true,
              as_of_date date not null,
              primary key (symbol, as_of_date)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists ohlcv_daily (
              symbol varchar not null,
              date date not null,
              open double not null,
              high double not null,
              low double not null,
              close double not null,
              volume double not null default 0,
              source varchar not null,
              primary key (symbol, date, source)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists ohlcv_hourly (
              symbol varchar not null,
              hour timestamp not null,
              open double not null,
              high double not null,
              low double not null,
              close double not null,
              volume double not null default 0,
              source varchar not null,
              primary key (symbol, hour, source)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists sync_state (
              symbol varchar not null,
              interval varchar not null,
              backfill_complete boolean not null default false,
              earliest_at varchar,
              latest_at varchar,
              last_refresh_at timestamp,
              primary key (symbol, interval)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists onchain_series (
              metric varchar not null,
              date date not null,
              value double not null,
              source varchar not null,
              primary key (metric, date, source)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists onchain_sync_state (
              metric varchar not null,
              source varchar not null,
              latest_at varchar,
              last_refresh_at timestamp,
              primary key (metric, source)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists open_interest (
              symbol varchar not null,
              timestamp timestamp not null,
              open_interest double not null,
              exchange varchar not null,
              unit varchar not null,
              interval varchar not null,
              primary key (symbol, timestamp, exchange, interval)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists macro_series (
              series_id varchar not null,
              date date not null,
              value double not null,
              source varchar not null,
              primary key (series_id, date, source)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists macro_sync_state (
              series_id varchar not null,
              source varchar not null,
              latest_at varchar,
              last_refresh_at timestamp,
              primary key (series_id, source)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists wallet_registry (
              address varchar not null,
              chain varchar not null,
              label varchar not null,
              entity_type varchar not null,
              confidence double not null,
              source varchar not null,
              discovered_at timestamp not null,
              active boolean not null default true,
              metadata_json varchar not null default '{}',
              primary key (address, chain)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists wallet_transfers (
              chain varchar not null,
              tx_hash varchar not null,
              transfer_index integer not null,
              block_time timestamp not null,
              from_address varchar not null,
              to_address varchar not null,
              asset_mint_or_contract varchar not null,
              asset_symbol varchar,
              amount double not null,
              amount_usd double,
              direction varchar not null,
              program_or_method varchar,
              source varchar not null,
              primary key (
                chain, tx_hash, transfer_index, from_address,
                to_address, asset_mint_or_contract
              )
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists wallet_positions_daily (
              address varchar not null,
              chain varchar not null,
              date date not null,
              asset_mint varchar not null,
              balance double not null,
              balance_usd double,
              source varchar not null,
              primary key (address, chain, date, asset_mint)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists wallet_sync_state (
              address varchar not null,
              chain varchar not null,
              source varchar not null,
              backfill_complete boolean not null default false,
              earliest_at timestamp,
              latest_at timestamp,
              last_refresh_at timestamp,
              primary key (address, chain, source)
            )
            """
        )
        # Legacy unused table: dbt fct_wallet_signals_daily is authoritative.
        # Kept for idempotent opens of older DuckDB files.
        self._conn.execute(
            """
            create table if not exists wallet_signals_daily (
              date date not null,
              chain varchar not null,
              smart_money_netflow_usd double not null default 0,
              kol_buy_count integer not null default 0,
              deployer_activity_count integer not null default 0,
              cabal_alert_count integer not null default 0,
              top_wallet_accumulation_score double not null default 0,
              primary key (date, chain)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists wallet_alerts (
              address varchar not null,
              chain varchar not null,
              mint_or_contract varchar not null,
              action varchar not null,
              severity varchar not null,
              block_time timestamp not null,
              tx_hash varchar not null,
              alerted_at timestamp not null,
              metadata_json varchar not null default '{}',
              primary key (address, chain, tx_hash, action)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists wallet_identities (
              identity_id varchar not null,
              display_name varchar not null,
              category varchar not null,
              description varchar not null default '',
              source_url varchar not null default '',
              active boolean not null default true,
              primary key (identity_id)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists wallet_identity_links (
              address varchar not null,
              chain varchar not null,
              identity_id varchar not null,
              link_type varchar not null,
              confidence double not null,
              source varchar not null,
              linked_at timestamp not null,
              primary key (address, chain, identity_id)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists twitter_accounts (
              handle varchar not null,
              user_id varchar,
              display_name varchar not null,
              entity_type varchar not null,
              chains varchar not null default '',
              symbols_watch varchar not null default '',
              confidence double not null,
              source varchar not null,
              active boolean not null default true,
              metadata_json varchar not null default '{}',
              primary key (handle)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists tweets (
              tweet_id varchar not null,
              handle varchar not null,
              posted_at timestamp not null,
              text varchar not null,
              lang varchar,
              is_retweet boolean not null default false,
              is_reply boolean not null default false,
              reply_to_tweet_id varchar,
              conversation_id varchar,
              like_count integer not null default 0,
              retweet_count integer not null default 0,
              reply_count integer not null default 0,
              import_source varchar not null,
              imported_at timestamp not null,
              raw_json varchar not null default '{}',
              primary key (tweet_id)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists tweet_entities (
              tweet_id varchar not null,
              entity_type varchar not null,
              entity_value varchar not null,
              primary key (tweet_id, entity_type, entity_value)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists tweet_sync_state (
              handle varchar not null,
              earliest_at timestamp,
              latest_at timestamp,
              latest_tweet_id varchar,
              last_import_at timestamp,
              backfill_complete boolean not null default false,
              primary key (handle)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists tweet_signals_daily (
              date date not null,
              symbol varchar not null,
              mention_count integer not null default 0,
              kol_mention_count integer not null default 0,
              bullish_keyword_count integer not null default 0,
              bearish_keyword_count integer not null default 0,
              unique_accounts integer not null default 0,
              primary key (date, symbol)
            )
            """
        )
        self._conn.execute(
            """
            create table if not exists tweet_alerts (
              tweet_id varchar not null,
              handle varchar not null,
              alert_type varchar not null,
              severity varchar not null,
              symbols varchar not null default '',
              posted_at timestamp not null,
              alerted_at timestamp not null,
              metadata_json varchar not null default '{}',
              primary key (tweet_id, alert_type)
            )
            """
        )

    def replace_assets(self, assets: list[Asset], as_of: date) -> None:
        self._conn.execute("update assets set active = false")
        for asset in assets:
            self._conn.execute(
                """
                insert into assets (
                  rank, symbol, coingecko_id, binance_pair, coinbase_product_id,
                  active, as_of_date
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict (symbol, as_of_date) do update set
                  rank = excluded.rank,
                  coingecko_id = excluded.coingecko_id,
                  binance_pair = excluded.binance_pair,
                  coinbase_product_id = excluded.coinbase_product_id,
                  active = excluded.active
                """,
                [
                    asset.rank,
                    asset.symbol.upper(),
                    asset.coingecko_id,
                    asset.binance_pair,
                    asset.coinbase_product_id,
                    asset.active,
                    as_of,
                ],
            )

    def active_assets(self, *, limit: int | None = None) -> list[Asset]:
        row = self._conn.execute("select max(as_of_date) from assets").fetchone()
        if row is None or row[0] is None:
            return []
        as_of = row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        sql = """
            select rank, symbol, coingecko_id, binance_pair, coinbase_product_id,
                   active, as_of_date
            from assets
            where as_of_date = ? and active = true
            order by rank asc
        """
        if limit is not None:
            sql += " limit ?"
            rows = self._conn.execute(sql, [as_of, limit]).fetchall()
        else:
            rows = self._conn.execute(sql, [as_of]).fetchall()
        return [
            Asset(
                rank=int(row[0]),
                symbol=str(row[1]),
                coingecko_id=str(row[2]),
                binance_pair=str(row[3]) if row[3] else None,
                coinbase_product_id=str(row[4]) if row[4] else None,
                active=bool(row[5]),
                as_of_date=row[6]
                if isinstance(row[6], date)
                else date.fromisoformat(str(row[6])),
            )
            for row in rows
        ]

    def upsert_daily(self, candles: list[DailyOhlcv]) -> int:
        for candle in candles:
            self._conn.execute(
                """
                insert into ohlcv_daily (
                  symbol, date, open, high, low, close, volume, source
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (symbol, date, source) do update set
                  open = excluded.open,
                  high = excluded.high,
                  low = excluded.low,
                  close = excluded.close,
                  volume = excluded.volume
                """,
                [
                    candle.symbol.upper(),
                    candle.date,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.source,
                ],
            )
        return len(candles)

    def upsert_hourly(self, candles: list[HourlyOhlcv]) -> int:
        for candle in candles:
            self._conn.execute(
                """
                insert into ohlcv_hourly (
                  symbol, hour, open, high, low, close, volume, source
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (symbol, hour, source) do update set
                  open = excluded.open,
                  high = excluded.high,
                  low = excluded.low,
                  close = excluded.close,
                  volume = excluded.volume
                """,
                [
                    candle.symbol.upper(),
                    candle.hour,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    candle.source,
                ],
            )
        return len(candles)

    def get_state(self, symbol: str, interval: Interval) -> SyncState | None:
        row = self._conn.execute(
            """
            select symbol, interval, backfill_complete, earliest_at, latest_at,
                   last_refresh_at
            from sync_state
            where symbol = ? and interval = ?
            """,
            [symbol.upper(), interval],
        ).fetchone()
        return None if row is None else self._row_to_state(row)

    def upsert_state(self, state: SyncState) -> None:
        self._conn.execute(
            """
            insert into sync_state (
              symbol, interval, backfill_complete, earliest_at, latest_at,
              last_refresh_at
            ) values (?, ?, ?, ?, ?, ?)
            on conflict (symbol, interval) do update set
              backfill_complete = excluded.backfill_complete,
              earliest_at = excluded.earliest_at,
              latest_at = excluded.latest_at,
              last_refresh_at = excluded.last_refresh_at
            """,
            [
                state.symbol.upper(),
                state.interval,
                state.backfill_complete,
                state.earliest_at.isoformat() if state.earliest_at else None,
                state.latest_at.isoformat() if state.latest_at else None,
                state.last_refresh_at,
            ],
        )

    def status_rows(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            """
            select a.symbol, a.rank,
                   count(distinct d.date) as daily_rows,
                   min(d.date) as daily_from,
                   max(d.date) as daily_to,
                   count(distinct h.hour) as hourly_rows,
                   min(h.hour) as hourly_from,
                   max(h.hour) as hourly_to
            from assets a
            left join ohlcv_daily d using (symbol)
            left join ohlcv_hourly h using (symbol)
            where a.active = true
              and a.as_of_date = (select max(as_of_date) from assets)
            group by a.symbol, a.rank
            order by a.rank asc
            """
        ).fetchall()
        return [
            {
                "symbol": row[0],
                "rank": row[1],
                "daily_rows": row[2],
                "daily_from": row[3],
                "daily_to": row[4],
                "hourly_rows": row[5],
                "hourly_from": row[6],
                "hourly_to": row[7],
            }
            for row in rows
        ]

    def export_table(self, table: str, out: Path, *, fmt: str) -> Path:
        out.mkdir(parents=True, exist_ok=True)
        suffix = "parquet" if fmt == "parquet" else "csv"
        dest = out / f"{table}.{suffix}"
        if fmt == "parquet":
            self._conn.execute(
                f"copy (select * from {table}) to ? (format parquet)",
                [str(dest)],
            )
        else:
            self._conn.execute(
                f"copy (select * from {table}) to ? (header true)",
                [str(dest)],
            )
        return dest

    def upsert_onchain_series(self, points: list[OnchainPoint]) -> int:
        for point in points:
            self._conn.execute(
                """
                insert into onchain_series (metric, date, value, source)
                values (?, ?, ?, ?)
                on conflict (metric, date, source) do update set
                  value = excluded.value
                """,
                [point.metric, point.date, point.value, point.source],
            )
        return len(points)

    def upsert_open_interest(self, points: list[OpenInterest]) -> int:
        for point in points:
            self._conn.execute(
                """
                insert into open_interest (
                  symbol, timestamp, open_interest, exchange, unit, interval
                ) values (?, ?, ?, ?, ?, ?)
                on conflict (symbol, timestamp, exchange, interval) do update set
                  open_interest = excluded.open_interest,
                  unit = excluded.unit
                """,
                [
                    point.symbol.upper(),
                    point.timestamp,
                    point.open_interest,
                    point.exchange,
                    point.unit,
                    point.interval,
                ],
            )
        return len(points)

    def upsert_macro_series(self, points: list[MacroPoint]) -> int:
        for point in points:
            self._conn.execute(
                """
                insert into macro_series (series_id, date, value, source)
                values (?, ?, ?, ?)
                on conflict (series_id, date, source) do update set
                  value = excluded.value
                """,
                [point.series_id, point.date, point.value, point.source],
            )
        return len(points)

    def backup(self, dest_dir: Path, *, keep: int = 10) -> Path:
        self._conn.execute("checkpoint")
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"ccquant-{timestamp}.duckdb"
        import shutil

        shutil.copy2(str(self.path), str(dest))
        backups = sorted(dest_dir.glob("ccquant-*.duckdb"), reverse=True)
        for old in backups[keep:]:
            old.unlink()
        return dest

    def migrate_onchain(self, source_db: str | Path) -> dict[str, int]:
        source_path = Path(source_db)
        if not source_path.exists():
            raise FileNotFoundError(f"source DB not found: {source_path}")
        escaped = source_path.as_posix().replace("'", "''")
        self._conn.execute(
            f"attach '{escaped}' as _src (read_only)"
        )
        try:
            self._conn.execute(
                """
                insert into onchain_series (metric, date, value, source)
                select metric, date, value, source from _src.onchain_series
                on conflict (metric, date, source) do update set
                  value = excluded.value
                """
            )
            series_row = self._conn.execute(
                "select count(*) from onchain_series"
            ).fetchone()
            series_count = int(series_row[0]) if series_row else 0
            self._conn.execute(
                """
                insert into onchain_sync_state (metric, source, latest_at,
                  last_refresh_at)
                select metric, source, latest_at, last_refresh_at
                from _src.onchain_sync_state
                on conflict (metric, source) do update set
                  latest_at = excluded.latest_at,
                  last_refresh_at = excluded.last_refresh_at
                """
            )
            state_row = self._conn.execute(
                "select count(*) from onchain_sync_state"
            ).fetchone()
            state_count = int(state_row[0]) if state_row else 0
        finally:
            self._conn.execute("detach _src")
        return {
            "onchain_series": int(series_count),
            "onchain_sync_state": int(state_count),
        }

    def onchain_row_counts(self) -> dict[str, int]:
        series_row = self._conn.execute(
            "select count(*) from onchain_series"
        ).fetchone()
        state_row = self._conn.execute(
            "select count(*) from onchain_sync_state"
        ).fetchone()
        return {
            "onchain_series": int(series_row[0]) if series_row else 0,
            "onchain_sync_state": int(state_row[0]) if state_row else 0,
        }

    def upsert_wallet_registry(self, entries: list[WalletRegistryEntry]) -> int:
        for entry in entries:
            self._conn.execute(
                """
                insert into wallet_registry (
                  address, chain, label, entity_type, confidence, source,
                  discovered_at, active, metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (address, chain) do update set
                  label = excluded.label,
                  entity_type = excluded.entity_type,
                  confidence = excluded.confidence,
                  source = excluded.source,
                  discovered_at = excluded.discovered_at,
                  active = excluded.active,
                  metadata_json = excluded.metadata_json
                """,
                [
                    entry.address,
                    entry.chain.lower(),
                    entry.label,
                    entry.entity_type,
                    entry.confidence,
                    entry.source,
                    entry.discovered_at,
                    entry.active,
                    entry.metadata_json,
                ],
            )
        return len(entries)

    def upsert_wallet_identities(self, identities: list[WalletIdentity]) -> int:
        for identity in identities:
            self._conn.execute(
                """
                insert into wallet_identities (
                  identity_id, display_name, category, description,
                  source_url, active
                ) values (?, ?, ?, ?, ?, ?)
                on conflict (identity_id) do update set
                  display_name = excluded.display_name,
                  category = excluded.category,
                  description = excluded.description,
                  source_url = excluded.source_url,
                  active = excluded.active
                """,
                [
                    identity.identity_id,
                    identity.display_name,
                    identity.category,
                    identity.description,
                    identity.source_url,
                    identity.active,
                ],
            )
        return len(identities)

    def upsert_wallet_identity_links(
        self,
        links: list[WalletIdentityLink],
    ) -> int:
        for link in links:
            self._conn.execute(
                """
                insert into wallet_identity_links (
                  address, chain, identity_id, link_type, confidence,
                  source, linked_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict (address, chain, identity_id) do update set
                  link_type = excluded.link_type,
                  confidence = excluded.confidence,
                  source = excluded.source
                """,
                [
                    link.address,
                    link.chain.lower(),
                    link.identity_id,
                    link.link_type,
                    link.confidence,
                    link.source,
                    link.linked_at,
                ],
            )
        return len(links)

    def active_wallet_registry(self) -> list[WalletRegistryEntry]:
        rows = self._conn.execute(
            """
            select address, chain, label, entity_type, confidence, source,
                   discovered_at, active, metadata_json
            from wallet_registry
            where active = true
            order by chain, entity_type, label
            """
        ).fetchall()
        return [self._row_to_wallet_registry(row) for row in rows]

    def active_wallet_addresses(self, *, chain: str) -> list[str]:
        rows = self._conn.execute(
            """
            select address
            from wallet_registry
            where active = true and chain = ?
            order by confidence desc, label asc
            """,
            [chain.lower()],
        ).fetchall()
        return [str(row[0]) for row in rows]

    def upsert_wallet_transfers(self, transfers: list[WalletTransfer]) -> int:
        for transfer in transfers:
            self._conn.execute(
                """
                insert into wallet_transfers (
                  chain, tx_hash, transfer_index, block_time, from_address,
                  to_address, asset_mint_or_contract, asset_symbol, amount,
                  amount_usd, direction, program_or_method, source
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (
                  chain, tx_hash, transfer_index, from_address,
                  to_address, asset_mint_or_contract
                ) do update set
                  block_time = excluded.block_time,
                  asset_symbol = excluded.asset_symbol,
                  amount = excluded.amount,
                  amount_usd = excluded.amount_usd,
                  direction = excluded.direction,
                  program_or_method = excluded.program_or_method,
                  source = excluded.source
                """,
                [
                    transfer.chain,
                    transfer.tx_hash,
                    transfer.transfer_index,
                    transfer.block_time,
                    transfer.from_address,
                    transfer.to_address,
                    transfer.asset_mint_or_contract,
                    transfer.asset_symbol,
                    transfer.amount,
                    transfer.amount_usd,
                    transfer.direction,
                    transfer.program_or_method,
                    transfer.source,
                ],
            )
        return len(transfers)

    def upsert_wallet_positions_daily(
        self,
        positions: list[WalletPositionDaily],
    ) -> int:
        for position in positions:
            self._conn.execute(
                """
                insert into wallet_positions_daily (
                  address, chain, date, asset_mint, balance, balance_usd, source
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict (address, chain, date, asset_mint) do update set
                  balance = excluded.balance,
                  balance_usd = excluded.balance_usd,
                  source = excluded.source
                """,
                [
                    position.address,
                    position.chain,
                    position.date,
                    position.asset_mint,
                    position.balance,
                    position.balance_usd,
                    position.source,
                ],
            )
        return len(positions)

    def get_wallet_sync_state(
        self,
        address: str,
        chain: str,
        source: str,
    ) -> WalletSyncState | None:
        row = self._conn.execute(
            """
            select address, chain, source, backfill_complete, earliest_at,
                   latest_at, last_refresh_at
            from wallet_sync_state
            where address = ? and chain = ? and source = ?
            """,
            [address, chain.lower(), source],
        ).fetchone()
        return None if row is None else self._row_to_wallet_sync_state(row)

    def upsert_wallet_sync_state(self, state: WalletSyncState) -> None:
        self._conn.execute(
            """
            insert into wallet_sync_state (
              address, chain, source, backfill_complete, earliest_at,
              latest_at, last_refresh_at
            ) values (?, ?, ?, ?, ?, ?, ?)
            on conflict (address, chain, source) do update set
              backfill_complete = excluded.backfill_complete,
              earliest_at = excluded.earliest_at,
              latest_at = excluded.latest_at,
              last_refresh_at = excluded.last_refresh_at
            """,
            [
                state.address,
                state.chain.lower(),
                state.source,
                state.backfill_complete,
                state.earliest_at,
                state.latest_at,
                state.last_refresh_at,
            ],
        )

    def wallet_sync_states(self) -> list[WalletSyncState]:
        rows = self._conn.execute(
            """
            select address, chain, source, backfill_complete, earliest_at,
                   latest_at, last_refresh_at
            from wallet_sync_state
            """
        ).fetchall()
        return [self._row_to_wallet_sync_state(row) for row in rows]

    def upsert_wallet_alerts(self, alerts: list[WalletAlert]) -> int:
        for alert in alerts:
            self._conn.execute(
                """
                insert into wallet_alerts (
                  address, chain, mint_or_contract, action, severity,
                  block_time, tx_hash, alerted_at, metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (address, chain, tx_hash, action) do update set
                  severity = excluded.severity,
                  block_time = excluded.block_time,
                  alerted_at = excluded.alerted_at,
                  metadata_json = excluded.metadata_json
                """,
                [
                    alert.address,
                    alert.chain,
                    alert.mint_or_contract,
                    alert.action,
                    alert.severity,
                    alert.block_time,
                    alert.tx_hash,
                    alert.alerted_at,
                    alert.metadata_json,
                ],
            )
        return len(alerts)

    def wallet_alerts_since(self, since: datetime) -> list[WalletAlert]:
        rows = self._conn.execute(
            """
            select address, chain, mint_or_contract, action, severity,
                   block_time, tx_hash, alerted_at, metadata_json
            from wallet_alerts
            where block_time >= ?
            order by block_time desc
            """,
            [since],
        ).fetchall()
        return [self._row_to_wallet_alert(row) for row in rows]

    def wallet_row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in [
            "wallet_registry",
            "wallet_transfers",
            "wallet_positions_daily",
            "wallet_sync_state",
            "wallet_alerts",
            "wallet_identities",
            "wallet_identity_links",
        ]:
            row = self._conn.execute(f"select count(*) from {table}").fetchone()
            counts[table] = int(row[0]) if row else 0
        return counts

    def upsert_twitter_accounts(self, accounts: list[TwitterAccount]) -> int:
        for account in accounts:
            self._conn.execute(
                """
                insert into twitter_accounts (
                  handle, user_id, display_name, entity_type, chains,
                  symbols_watch, confidence, source, active, metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (handle) do update set
                  user_id = coalesce(excluded.user_id, twitter_accounts.user_id),
                  display_name = excluded.display_name,
                  entity_type = excluded.entity_type,
                  chains = excluded.chains,
                  symbols_watch = excluded.symbols_watch,
                  confidence = excluded.confidence,
                  source = excluded.source,
                  active = excluded.active,
                  metadata_json = excluded.metadata_json
                """,
                [
                    account.handle,
                    account.user_id,
                    account.display_name,
                    account.entity_type,
                    account.chains,
                    account.symbols_watch,
                    account.confidence,
                    account.source,
                    account.active,
                    account.metadata_json,
                ],
            )
        return len(accounts)

    def active_twitter_accounts(self) -> list[TwitterAccount]:
        rows = self._conn.execute(
            """
            select handle, user_id, display_name, entity_type, chains,
                   symbols_watch, confidence, source, active, metadata_json
            from twitter_accounts
            where active = true
            order by entity_type, handle
            """
        ).fetchall()
        return [self._row_to_twitter_account(row) for row in rows]

    def all_twitter_accounts(self) -> list[TwitterAccount]:
        rows = self._conn.execute(
            """
            select handle, user_id, display_name, entity_type, chains,
                   symbols_watch, confidence, source, active, metadata_json
            from twitter_accounts
            order by active desc, entity_type, handle
            """
        ).fetchall()
        return [self._row_to_twitter_account(row) for row in rows]

    def discovered_twitter_accounts(self) -> list[TwitterAccount]:
        rows = self._conn.execute(
            """
            select handle, user_id, display_name, entity_type, chains,
                   symbols_watch, confidence, source, active, metadata_json
            from twitter_accounts
            where source = 'import_discovered' and active = false
            order by handle
            """
        ).fetchall()
        return [self._row_to_twitter_account(row) for row in rows]

    def promote_twitter_account(self, handle: str) -> bool:
        row = self._conn.execute(
            """
            update twitter_accounts
            set active = true, source = 'manual', confidence = 0.6
            where handle = ?
            returning handle
            """,
            [handle.lower()],
        ).fetchone()
        return row is not None

    def upsert_tweets(
        self,
        tweets: list[Tweet],
        *,
        on_conflict: str = "skip",
    ) -> int:
        inserted = 0
        for tweet in tweets:
            if on_conflict == "skip":
                existing = self._conn.execute(
                    "select tweet_id from tweets where tweet_id = ?",
                    [tweet.tweet_id],
                ).fetchone()
                if existing is not None:
                    continue
                self._conn.execute(
                    """
                    insert into tweets (
                      tweet_id, handle, posted_at, text, lang, is_retweet, is_reply,
                      reply_to_tweet_id, conversation_id, like_count, retweet_count,
                      reply_count, import_source, imported_at, raw_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        tweet.tweet_id,
                        tweet.handle,
                        tweet.posted_at,
                        tweet.text,
                        tweet.lang,
                        tweet.is_retweet,
                        tweet.is_reply,
                        tweet.reply_to_tweet_id,
                        tweet.conversation_id,
                        tweet.like_count,
                        tweet.retweet_count,
                        tweet.reply_count,
                        tweet.import_source,
                        tweet.imported_at,
                        tweet.raw_json,
                    ],
                )
            else:
                self._conn.execute(
                    """
                    insert into tweets (
                      tweet_id, handle, posted_at, text, lang, is_retweet, is_reply,
                      reply_to_tweet_id, conversation_id, like_count, retweet_count,
                      reply_count, import_source, imported_at, raw_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict (tweet_id) do update set
                      like_count = excluded.like_count,
                      retweet_count = excluded.retweet_count,
                      reply_count = excluded.reply_count,
                      imported_at = excluded.imported_at
                    """,
                    [
                        tweet.tweet_id,
                        tweet.handle,
                        tweet.posted_at,
                        tweet.text,
                        tweet.lang,
                        tweet.is_retweet,
                        tweet.is_reply,
                        tweet.reply_to_tweet_id,
                        tweet.conversation_id,
                        tweet.like_count,
                        tweet.retweet_count,
                        tweet.reply_count,
                        tweet.import_source,
                        tweet.imported_at,
                        tweet.raw_json,
                    ],
                )
            inserted += 1
        return inserted

    def all_tweets(self) -> list[Tweet]:
        rows = self._conn.execute(
            """
            select tweet_id, handle, posted_at, text, lang, is_retweet, is_reply,
                   reply_to_tweet_id, conversation_id, like_count, retweet_count,
                   reply_count, import_source, imported_at, raw_json
            from tweets
            order by posted_at asc
            """
        ).fetchall()
        return [self._row_to_tweet(row) for row in rows]

    def tweets_needing_enrichment(self) -> list[Tweet]:
        rows = self._conn.execute(
            """
            select t.tweet_id, t.handle, t.posted_at, t.text, t.lang, t.is_retweet,
                   t.is_reply, t.reply_to_tweet_id, t.conversation_id, t.like_count,
                   t.retweet_count, t.reply_count, t.import_source, t.imported_at,
                   t.raw_json
            from tweets t
            left join tweet_entities e on t.tweet_id = e.tweet_id
            where e.tweet_id is null
            order by t.posted_at asc
            """
        ).fetchall()
        return [self._row_to_tweet(row) for row in rows]

    def upsert_tweet_entities(self, entities: list[TweetEntity]) -> int:
        for entity in entities:
            self._conn.execute(
                """
                insert into tweet_entities (tweet_id, entity_type, entity_value)
                values (?, ?, ?)
                on conflict (tweet_id, entity_type, entity_value) do nothing
                """,
                [entity.tweet_id, entity.entity_type, entity.entity_value],
            )
        return len(entities)

    def tweet_entities_for(self, tweet_id: str) -> list[TweetEntity]:
        rows = self._conn.execute(
            """
            select tweet_id, entity_type, entity_value
            from tweet_entities
            where tweet_id = ?
            """,
            [tweet_id],
        ).fetchall()
        return [
            TweetEntity(
                tweet_id=str(row[0]),
                entity_type=str(row[1]),
                entity_value=str(row[2]),
            )
            for row in rows
        ]

    def get_tweet_sync_state(self, handle: str) -> TweetSyncState | None:
        row = self._conn.execute(
            """
            select handle, earliest_at, latest_at, latest_tweet_id,
                   last_import_at, backfill_complete
            from tweet_sync_state
            where handle = ?
            """,
            [handle.lower()],
        ).fetchone()
        return None if row is None else self._row_to_tweet_sync_state(row)

    def upsert_tweet_sync_state(self, state: TweetSyncState) -> None:
        self._conn.execute(
            """
            insert into tweet_sync_state (
              handle, earliest_at, latest_at, latest_tweet_id,
              last_import_at, backfill_complete
            ) values (?, ?, ?, ?, ?, ?)
            on conflict (handle) do update set
              earliest_at = excluded.earliest_at,
              latest_at = excluded.latest_at,
              latest_tweet_id = excluded.latest_tweet_id,
              last_import_at = excluded.last_import_at,
              backfill_complete = excluded.backfill_complete
            """,
            [
                state.handle,
                state.earliest_at,
                state.latest_at,
                state.latest_tweet_id,
                state.last_import_at,
                state.backfill_complete,
            ],
        )

    def upsert_tweet_signals_daily(self, signals: list[TweetSignalDaily]) -> int:
        for signal in signals:
            self._conn.execute(
                """
                insert into tweet_signals_daily (
                  date, symbol, mention_count, kol_mention_count,
                  bullish_keyword_count, bearish_keyword_count, unique_accounts
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict (date, symbol) do update set
                  mention_count = excluded.mention_count,
                  kol_mention_count = excluded.kol_mention_count,
                  bullish_keyword_count = excluded.bullish_keyword_count,
                  bearish_keyword_count = excluded.bearish_keyword_count,
                  unique_accounts = excluded.unique_accounts
                """,
                [
                    signal.date,
                    signal.symbol,
                    signal.mention_count,
                    signal.kol_mention_count,
                    signal.bullish_keyword_count,
                    signal.bearish_keyword_count,
                    signal.unique_accounts,
                ],
            )
        return len(signals)

    def all_tweet_signals_daily(self) -> list[TweetSignalDaily]:
        rows = self._conn.execute(
            """
            select date, symbol, mention_count, kol_mention_count,
                   bullish_keyword_count, bearish_keyword_count, unique_accounts
            from tweet_signals_daily
            order by date asc, symbol asc
            """
        ).fetchall()
        return [self._row_to_tweet_signal(row) for row in rows]

    def upsert_tweet_alerts(self, alerts: list[TweetAlert]) -> int:
        for alert in alerts:
            self._conn.execute(
                """
                insert into tweet_alerts (
                  tweet_id, handle, alert_type, severity, symbols,
                  posted_at, alerted_at, metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict (tweet_id, alert_type) do update set
                  severity = excluded.severity,
                  symbols = excluded.symbols,
                  posted_at = excluded.posted_at,
                  alerted_at = excluded.alerted_at,
                  metadata_json = excluded.metadata_json
                """,
                [
                    alert.tweet_id,
                    alert.handle,
                    alert.alert_type,
                    alert.severity,
                    alert.symbols,
                    alert.posted_at,
                    alert.alerted_at,
                    alert.metadata_json,
                ],
            )
        return len(alerts)

    def tweet_alerts_since(self, since: datetime) -> list[TweetAlert]:
        rows = self._conn.execute(
            """
            select tweet_id, handle, alert_type, severity, symbols,
                   posted_at, alerted_at, metadata_json
            from tweet_alerts
            where posted_at >= ?
            order by posted_at desc
            """,
            [since],
        ).fetchall()
        return [self._row_to_tweet_alert(row) for row in rows]

    def twitter_row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in [
            "twitter_accounts",
            "tweets",
            "tweet_entities",
            "tweet_sync_state",
            "tweet_signals_daily",
            "tweet_alerts",
        ]:
            row = self._conn.execute(f"select count(*) from {table}").fetchone()
            counts[table] = int(row[0]) if row else 0
        return counts

    @staticmethod
    def _row_to_twitter_account(row: tuple[object, ...]) -> TwitterAccount:
        return TwitterAccount(
            handle=str(row[0]),
            user_id=str(row[1]) if row[1] else None,
            display_name=str(row[2]),
            entity_type=str(row[3]),
            chains=str(row[4]),
            symbols_watch=str(row[5]),
            confidence=float(str(row[6])),
            source=str(row[7]),
            active=bool(row[8]),
            metadata_json=str(row[9]),
        )

    @staticmethod
    def _row_to_tweet(row: tuple[object, ...]) -> Tweet:
        posted_at = _parse_datetime(row[2])
        imported_at = _parse_datetime(row[13])
        return Tweet(
            tweet_id=str(row[0]),
            handle=str(row[1]),
            posted_at=posted_at or datetime.now(tz=UTC),
            text=str(row[3]),
            lang=str(row[4]) if row[4] else None,
            is_retweet=bool(row[5]),
            is_reply=bool(row[6]),
            reply_to_tweet_id=str(row[7]) if row[7] else None,
            conversation_id=str(row[8]) if row[8] else None,
            like_count=int(str(row[9])),
            retweet_count=int(str(row[10])),
            reply_count=int(str(row[11])),
            import_source=str(row[12]),
            imported_at=imported_at or datetime.now(tz=UTC),
            raw_json=str(row[14]),
        )

    @staticmethod
    def _row_to_tweet_sync_state(row: tuple[object, ...]) -> TweetSyncState:
        return TweetSyncState(
            handle=str(row[0]),
            earliest_at=_parse_datetime(row[1]),
            latest_at=_parse_datetime(row[2]),
            latest_tweet_id=str(row[3]) if row[3] else None,
            last_import_at=_parse_datetime(row[4]),
            backfill_complete=bool(row[5]),
        )

    @staticmethod
    def _row_to_tweet_signal(row: tuple[object, ...]) -> TweetSignalDaily:
        day = row[0]
        if not isinstance(day, date):
            day = date.fromisoformat(str(day))
        return TweetSignalDaily(
            date=day,
            symbol=str(row[1]),
            mention_count=int(str(row[2])),
            kol_mention_count=int(str(row[3])),
            bullish_keyword_count=int(str(row[4])),
            bearish_keyword_count=int(str(row[5])),
            unique_accounts=int(str(row[6])),
        )

    @staticmethod
    def _row_to_tweet_alert(row: tuple[object, ...]) -> TweetAlert:
        posted_at = _parse_datetime(row[5])
        alerted_at = _parse_datetime(row[6])
        return TweetAlert(
            tweet_id=str(row[0]),
            handle=str(row[1]),
            alert_type=str(row[2]),
            severity=str(row[3]),
            symbols=str(row[4]),
            posted_at=posted_at or datetime.now(tz=UTC),
            alerted_at=alerted_at or datetime.now(tz=UTC),
            metadata_json=str(row[7]),
        )

    @staticmethod
    def _row_to_wallet_registry(row: tuple[object, ...]) -> WalletRegistryEntry:
        discovered = row[6]
        if discovered is not None and not isinstance(discovered, datetime):
            discovered = datetime.fromisoformat(str(discovered))
            if discovered.tzinfo is None:
                discovered = discovered.replace(tzinfo=UTC)
        return WalletRegistryEntry(
            address=str(row[0]),
            chain=str(row[1]),
            label=str(row[2]),
            entity_type=str(row[3]),
            confidence=float(str(row[4])),
            source=str(row[5]),
            discovered_at=(
                discovered
                if isinstance(discovered, datetime)
                else datetime.now(tz=UTC)
            ),
            active=bool(row[7]),
            metadata_json=str(row[8]),
        )

    @staticmethod
    def _row_to_wallet_sync_state(row: tuple[object, ...]) -> WalletSyncState:
        return WalletSyncState(
            address=str(row[0]),
            chain=str(row[1]),
            source=str(row[2]),
            backfill_complete=bool(row[3]),
            earliest_at=_parse_datetime(row[4]),
            latest_at=_parse_datetime(row[5]),
            last_refresh_at=_parse_datetime(row[6]),
        )

    @staticmethod
    def _row_to_wallet_alert(row: tuple[object, ...]) -> WalletAlert:
        block_time = _parse_datetime(row[5])
        alerted_at = _parse_datetime(row[7])
        return WalletAlert(
            address=str(row[0]),
            chain=str(row[1]),
            mint_or_contract=str(row[2]),
            action=str(row[3]),
            severity=str(row[4]),
            block_time=block_time or datetime.now(tz=UTC),
            tx_hash=str(row[6]),
            alerted_at=alerted_at or datetime.now(tz=UTC),
            metadata_json=str(row[8]),
        )

    @staticmethod
    def _row_to_state(row: tuple[object, ...]) -> SyncState:
        last_refresh = row[5]
        if last_refresh is not None and not isinstance(last_refresh, datetime):
            last_refresh = datetime.fromisoformat(str(last_refresh))
            if last_refresh.tzinfo is None:
                last_refresh = last_refresh.replace(tzinfo=UTC)
        return SyncState(
            symbol=str(row[0]),
            interval=str(row[1]),
            backfill_complete=bool(row[2]),
            earliest_at=_parse_date_or_datetime(row[3]),
            latest_at=_parse_date_or_datetime(row[4]),
            last_refresh_at=last_refresh,
        )


def _parse_date_or_datetime(value: object) -> date | datetime | None:
    if value is None:
        return None
    raw = str(value)
    if "T" in raw or " " in raw:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return date.fromisoformat(raw)


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
