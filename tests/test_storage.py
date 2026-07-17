from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb

from ccquant.models import (
    Asset,
    DailyOhlcv,
    DexPriceDaily,
    HourlyOhlcv,
    MacroPoint,
    MevBoostPayload,
    OnchainPoint,
    OpenInterest,
    OrderBookSnapshot,
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


def test_upsert_order_book_snapshots_is_idempotent(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        ts = datetime(2026, 7, 1, 12, tzinfo=UTC)
        snaps = [
            OrderBookSnapshot(
                symbol="BTC",
                timestamp=ts,
                exchange="binance",
                mid=100.0,
                best_bid=99.9,
                best_ask=100.1,
                spread_bps=20.0,
                bid_notional_bps_10=100.0,
                ask_notional_bps_10=110.0,
                bid_notional_bps_25=200.0,
                ask_notional_bps_25=220.0,
                bid_notional_bps_50=300.0,
                ask_notional_bps_50=330.0,
                imbalance_bps_25=-0.05,
                depth_levels=4,
                last_update_id=1,
                fetched_at=ts,
            ),
            OrderBookSnapshot(
                symbol="BTC",
                timestamp=ts,
                exchange="binance",
                mid=101.0,
                best_bid=100.9,
                best_ask=101.1,
                spread_bps=19.8,
                bid_notional_bps_10=105.0,
                ask_notional_bps_10=115.0,
                bid_notional_bps_25=210.0,
                ask_notional_bps_25=230.0,
                bid_notional_bps_50=310.0,
                ask_notional_bps_50=340.0,
                imbalance_bps_25=-0.04,
                depth_levels=4,
                last_update_id=2,
                fetched_at=ts,
            ),
        ]
        assert store.upsert_order_book_snapshots(snaps) == 2
        row = store.connection.execute(
            "select mid, last_update_id from order_book_snapshots"
            " where symbol='BTC' and exchange='binance'"
        ).fetchone()
        assert row is not None and row[0] == 101.0 and row[1] == 2
        state = store.connection.execute(
            "select snapshot_count from order_book_sync_state"
            " where symbol='BTC' and exchange='binance'"
        ).fetchone()
        assert state is not None and state[0] == 2
    finally:
        store.close()


def test_upsert_dex_and_mev_boost(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        assert (
            store.upsert_dex_price_daily(
                [
                    DexPriceDaily(
                        symbol="ETH",
                        date=date(2026, 7, 1),
                        venue="defillama",
                        price_usd=3000.0,
                        source="defillama",
                    )
                ]
            )
            == 1
        )
        assert (
            store.upsert_mev_boost_payloads(
                [
                    MevBoostPayload(
                        slot=9_000_000,
                        block_number=20_000_000,
                        builder_pubkey="0xabc",
                        proposer_fee_recipient="0xdef",
                        value_wei=1e18,
                        value_eth=1.0,
                        relay="flashbots",
                        date=date(2026, 7, 1),
                        source="mevboost-data",
                    )
                ]
            )
            == 1
        )
        dex = store.connection.execute(
            "select price_usd from dex_price_daily where symbol='ETH'"
        ).fetchone()
        assert dex is not None and dex[0] == 3000.0
        mev = store.connection.execute(
            "select value_eth from mev_boost_payloads where slot=9000000"
        ).fetchone()
        assert mev is not None and mev[0] == 1.0
    finally:
        store.close()


def test_import_mev_boost_parquet(tmp_path) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    try:
        parquet_dir = tmp_path / "mevboost"
        parquet_dir.mkdir()
        store.connection.execute(
            f"""
            copy (
              select
                9000001::bigint as slot,
                20000001::bigint as block_number,
                '0xbuilder' as builder_pubkey,
                '0xfee' as proposer_fee_recipient,
                2e18::double as value,
                'flashbots' as relay,
                date '2026-07-01' as date
            ) to '{parquet_dir / "day.parquet"}' (format parquet)
            """
        )
        count = store.import_mev_boost_parquet(parquet_dir)
        assert count == 1
        row = store.connection.execute(
            "select value_eth, relay from mev_boost_payloads where slot=9000001"
        ).fetchone()
        assert row is not None
        assert row[0] == 2.0
        assert row[1] == "flashbots"
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

