from __future__ import annotations

from datetime import date

import httpx
import pytest

from ccquant.config import AppConfig, UniverseConfig
from ccquant.models import Asset
from ccquant.storage import MarketStore
from ccquant.sync import MarketSync


@pytest.mark.asyncio
async def test_backfill_records_zero_and_continues_on_http_error(
    tmp_path,
    monkeypatch,
) -> None:
    store = MarketStore(tmp_path / "ccquant.duckdb")
    as_of = date(2026, 7, 2)
    store.replace_assets(
        [
            Asset(
                rank=1,
                symbol="DOGE",
                coingecko_id="dogecoin",
                binance_pair=None,
                coinbase_product_id="DOGE-USD",
                active=True,
                as_of_date=as_of,
            ),
            Asset(
                rank=2,
                symbol="BTC",
                coingecko_id="bitcoin",
                binance_pair="BTCUSDT",
                coinbase_product_id="BTC-USD",
                active=True,
                as_of_date=as_of,
            ),
        ],
        as_of,
    )

    async def fake_daily(
        self: MarketSync, asset: Asset, *, full: bool, force: bool = False
    ) -> int:
        if asset.symbol == "DOGE":
            request = httpx.Request("GET", "https://api.coinbase.com")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError(
                "Service unavailable",
                request=request,
                response=response,
            )
        return 5

    monkeypatch.setattr(MarketSync, "backfill_daily", fake_daily)
    syncer = MarketSync(
        store,
        AppConfig(
            database=tmp_path / "ccquant.duckdb",
            universe=UniverseConfig(request_delay_seconds=0),
        ),
    )
    try:
        results = await syncer.backfill(interval="1d", full=True)
    finally:
        await syncer.close()
        store.close()

    assert results == {"DOGE": 0, "BTC": 5}


@pytest.mark.asyncio
async def test_backfill_force_reopens_completed_daily_history(
    tmp_path,
    monkeypatch,
) -> None:
    from ccquant.models import DailyOhlcv, SyncState

    store = MarketStore(tmp_path / "ccquant.duckdb")
    as_of = date(2026, 7, 2)
    store.replace_assets(
        [
            Asset(
                rank=1,
                symbol="ETH",
                coingecko_id="ethereum",
                binance_pair="ETHUSDT",
                coinbase_product_id="ETH-USD",
                active=True,
                as_of_date=as_of,
            ),
        ],
        as_of,
    )
    store.upsert_state(
        SyncState(
            symbol="ETH",
            interval="1d",
            backfill_complete=True,
            earliest_at=date(2026, 6, 12),
            latest_at=date(2026, 7, 2),
        )
    )

    seen_starts: list[date | None] = []

    async def fake_fetch(
        self: MarketSync,
        asset: Asset,
        *,
        start: date | None,
        end: date,
    ) -> list[DailyOhlcv]:
        seen_starts.append(start)
        return [
            DailyOhlcv(
                symbol="ETH",
                date=date(2020, 1, 1),
                open=100.0,
                high=110.0,
                low=90.0,
                close=105.0,
                volume=1.0,
                source="coinbase",
            ),
            DailyOhlcv(
                symbol="ETH",
                date=date(2026, 7, 2),
                open=3000.0,
                high=3100.0,
                low=2900.0,
                close=3050.0,
                volume=2.0,
                source="coinbase",
            ),
        ]

    monkeypatch.setattr(MarketSync, "_fetch_daily", fake_fetch)
    syncer = MarketSync(
        store,
        AppConfig(
            database=tmp_path / "ccquant.duckdb",
            universe=UniverseConfig(request_delay_seconds=0),
        ),
    )
    try:
        # Without force: still tail-only because backfill_complete is True
        await syncer.backfill(interval="1d", full=True, force=False)
        assert seen_starts[-1] is not None
        # With force: start=None for full historical pull
        await syncer.backfill(interval="1d", full=True, force=True)
        assert seen_starts[-1] is None
        state = store.get_state("ETH", "1d")
        assert state is not None
        assert state.backfill_complete is True
        assert state.earliest_at == date(2020, 1, 1)
    finally:
        await syncer.close()
        store.close()




def test_load_project_dotenv_override_strips_inline_comments(
    tmp_path, monkeypatch
) -> None:
    """python-dotenv strips inline comments; override=True beats polluted env."""
    import os

    from ccquant.config import load_project_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text(
        "FRED_API_KEY=abcd1234abcd1234abcd1234abcd1234  # https://example.com\n"
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\n")
    monkeypatch.setenv(
        "FRED_API_KEY",
        "polluted-key-from-vscode  # should be replaced",
    )
    monkeypatch.chdir(tmp_path)
    assert load_project_dotenv(tmp_path) == env_file
    assert os.environ["FRED_API_KEY"] == "abcd1234abcd1234abcd1234abcd1234"
