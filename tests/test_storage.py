from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb

from ccquant.models import (
    Asset,
    DailyOhlcv,
    HourlyOhlcv,
    MacroPoint,
    OnchainPoint,
    OpenInterest,
)
from ccquant.storage import MarketStore


def test_store_assets_and_ohlcv(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        as_of = date(2026, 7, 2)
        store.replace_assets(
            [
                Asset(
                    rank=1,
                    symbol="BTC",
                    coingecko_id="bitcoin",
                    binance_pair="BTCUSDT",
                    coinbase_product_id="BTC-USD",
                    active=True,
                    as_of_date=as_of,
                )
            ],
            as_of,
        )
        assert store.active_assets()[0].symbol == "BTC"

        assert store.upsert_daily(
            [
                DailyOhlcv(
                    symbol="BTC",
                    date=date(2026, 7, 1),
                    open=100.0,
                    high=110.0,
                    low=95.0,
                    close=105.0,
                    volume=10.0,
                    source="binance",
                )
            ]
        ) == 1
        assert store.upsert_hourly(
            [
                HourlyOhlcv(
                    symbol="BTC",
                    hour=datetime(2026, 7, 1, 12, tzinfo=UTC),
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1.0,
                    source="binance",
                )
            ]
        ) == 1
        status = store.status_rows()
        assert status[0]["daily_rows"] == 1
        assert status[0]["hourly_rows"] == 1
    finally:
        store.close()


def test_upsert_onchain_series_is_idempotent(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        points = [
            OnchainPoint(metric="mvrv", date=date(2026, 7, 1), value=2.1, source="bid"),
            OnchainPoint(metric="mvrv", date=date(2026, 7, 1), value=2.3, source="bid"),
        ]
        assert store.upsert_onchain_series(points) == 2
        counts = store.onchain_row_counts()
        assert counts["onchain_series"] == 1
        row = store.connection.execute(
            "select value from onchain_series where metric='mvrv' and date='2026-07-01'"
        ).fetchone()
        assert row is not None and row[0] == 2.3
    finally:
        store.close()


def test_migrate_onchain_from_external_db(tmp_path) -> None:
    src_path = tmp_path / "onchain.duckdb"
    conn = duckdb.connect(str(src_path))
    conn.execute(
        "create table onchain_series (metric varchar, date date, value double,"
        " source varchar, primary key (metric, date, source))"
    )
    conn.execute(
        "create table onchain_sync_state (metric varchar, source varchar,"
        " latest_at varchar, last_refresh_at timestamp,"
        " primary key (metric, source))"
    )
    conn.execute(
        "insert into onchain_series values"
        " ('mvrv', '2026-07-01', 2.1, 'bid'),"
        " ('nupl', '2026-07-01', 0.45, 'bid')"
    )
    conn.execute(
        "insert into onchain_sync_state values"
        " ('mvrv', 'bid', '2026-07-01', '2026-07-01T12:00:00')"
    )
    conn.close()

    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        counts = store.migrate_onchain(src_path)
        assert counts["onchain_series"] == 2
        assert counts["onchain_sync_state"] == 1
        counts2 = store.migrate_onchain(src_path)
        assert counts2["onchain_series"] == 2
    finally:
        store.close()


def test_backup_creates_file_and_rotates(tmp_path) -> None:
    import time

    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        dest = tmp_path / "backups"
        path = store.backup(dest, keep=2)
        assert path.exists()
        time.sleep(1.1)
        path2 = store.backup(dest, keep=2)
        assert path2.exists()
        assert path != path2
        time.sleep(1.1)
        store.backup(dest, keep=2)
        backups = sorted(dest.glob("ccquant-*.duckdb"))
        assert len(backups) <= 2
    finally:
        store.close()


def test_upsert_open_interest_is_idempotent(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        points = [
            OpenInterest(
                symbol="BTC",
                timestamp=datetime(2026, 7, 1, 12, tzinfo=UTC),
                open_interest=150_000_000.0,
                exchange="binance",
                unit="usd_notional",
                interval="1h",
            ),
            OpenInterest(
                symbol="BTC",
                timestamp=datetime(2026, 7, 1, 12, tzinfo=UTC),
                open_interest=155_000_000.0,
                exchange="binance",
                unit="usd_notional",
                interval="1h",
            ),
        ]
        assert store.upsert_open_interest(points) == 2
        row = store.connection.execute(
            "select open_interest from open_interest"
            " where symbol='BTC' and exchange='binance'"
        ).fetchone()
        assert row is not None and row[0] == 155_000_000.0
    finally:
        store.close()


def test_upsert_macro_series_is_idempotent(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        points = [
            MacroPoint(
                series_id="DGS10",
                date=date(2026, 7, 1),
                value=4.3,
                source="fred",
            ),
            MacroPoint(
                series_id="DGS10",
                date=date(2026, 7, 1),
                value=4.5,
                source="fred",
            ),
        ]
        assert store.upsert_macro_series(points) == 2
        row = store.connection.execute(
            "select value from macro_series"
            " where series_id='DGS10' and date='2026-07-01'"
        ).fetchone()
        assert row is not None and row[0] == 4.5
    finally:
        store.close()

