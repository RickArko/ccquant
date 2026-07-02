from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import duckdb

from ccquant.models import Asset, DailyOhlcv, HourlyOhlcv, SyncState

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
