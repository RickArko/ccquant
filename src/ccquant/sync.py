from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime, timedelta

import httpx

from ccquant.config import AppConfig
from ccquant.models import (
    Asset,
    DailyOhlcv,
    HourlyOhlcv,
    OpenInterest,
    SyncState,
)
from ccquant.sources import (
    coinbase_product_id,
    default_binance_pair,
    fetch_binance_daily,
    fetch_binance_hourly,
    fetch_binance_oi,
    fetch_bybit_oi,
    fetch_coinbase_daily,
    fetch_coinbase_hourly,
    fetch_coingecko_daily,
    fetch_fred_series,
    fetch_okx_oi,
    fetch_top_markets,
    probe_binance_pair,
    probe_coinbase_product,
)
from ccquant.storage import MarketStore

LOGGER = logging.getLogger(__name__)

KNOWN_COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "PAXG": "pax-gold",
    "HYPE": "hyperliquid",
}


class MarketSync:
    def __init__(
        self,
        store: MarketStore,
        config: AppConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self._client = http_client
        self._owns_client = http_client is None

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def update_universe(self, *, size: int | None = None) -> int:
        client = await self.client()
        today = datetime.now(tz=UTC).date()
        limit = size or self.config.universe.size
        markets = await fetch_top_markets(client, size=limit)
        assets: list[Asset] = []
        seen: set[str] = set()

        for item in markets:
            symbol = str(item["symbol"]).upper()
            if symbol in seen:
                continue
            seen.add(symbol)
            assets.append(await self._asset_from_market(item, len(assets) + 1, today))
            await asyncio.sleep(self.config.universe.request_delay_seconds)

        for symbol in self.config.universe.include_symbols:
            if symbol in seen:
                continue
            item = {
                "rank": len(assets) + 1,
                "symbol": symbol.upper(),
                "coingecko_id": KNOWN_COINGECKO_IDS.get(symbol.upper(), symbol.lower()),
            }
            assets.append(await self._asset_from_market(item, len(assets) + 1, today))
            seen.add(symbol)

        self.store.replace_assets(assets, today)
        return len(assets)

    async def backfill(
        self,
        *,
        interval: str,
        full: bool,
        top: int | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        if not self.store.active_assets():
            await self.update_universe()
        assets = self.store.active_assets(limit=top)
        results: dict[str, int] = {}
        for asset in assets:
            try:
                if interval == "1h":
                    results[asset.symbol] = await self.backfill_hourly(
                        asset, full=full, force=force
                    )
                else:
                    results[asset.symbol] = await self.backfill_daily(
                        asset, full=full, force=force
                    )
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "OHLCV fetch failed for %s %s: %s",
                    asset.symbol,
                    interval,
                    exc,
                )
                results[asset.symbol] = 0
            await asyncio.sleep(self.config.universe.request_delay_seconds)
        return results

    async def backfill_daily(
        self, asset: Asset, *, full: bool, force: bool = False
    ) -> int:
        today = datetime.now(tz=UTC).date()
        state = self.store.get_state(asset.symbol, "1d") or SyncState(
            symbol=asset.symbol,
            interval="1d",
        )
        if force:
            state.backfill_complete = False
        start = None if full and not state.backfill_complete else (
            today - timedelta(days=self.config.daily.tail_days)
        )
        candles = await self._fetch_daily(asset, start=start, end=today)
        count = self.store.upsert_daily(candles)
        _update_state(state, [c.date for c in candles], full=full)
        self.store.upsert_state(state)
        return count

    async def backfill_hourly(
        self, asset: Asset, *, full: bool, force: bool = False
    ) -> int:
        now = datetime.now(tz=UTC).replace(minute=0, second=0, microsecond=0)
        state = self.store.get_state(asset.symbol, "1h") or SyncState(
            symbol=asset.symbol,
            interval="1h",
        )
        hours = (
            self.config.hourly.history_days * 24
            if full or force
            else self.config.hourly.tail_hours
        )
        start = now - timedelta(hours=hours)
        candles = await self._fetch_hourly(asset, start=start, end=now)
        count = self.store.upsert_hourly(candles)
        _update_state(state, [c.hour for c in candles], full=full or force)
        self.store.upsert_state(state)
        return count

    async def backfill_open_interest(
        self,
        asset: Asset,
        *,
        interval: str = "1h",
        full: bool = False,
    ) -> int:
        oi_cfg = self.config.open_interest
        if not oi_cfg.enabled:
            return 0
        now = datetime.now(tz=UTC).replace(minute=0, second=0, microsecond=0)
        hours = (
            oi_cfg.history_days * 24
            if full
            else oi_cfg.tail_hours
        )
        start = now - timedelta(hours=hours)
        points: list[OpenInterest] = []
        if oi_cfg.binance and asset.binance_pair:
            try:
                points = await fetch_binance_oi(
                    await self.client(),
                    symbol=asset.symbol,
                    pair=asset.binance_pair,
                    interval=interval,
                    start=start,
                    end=now,
                )
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "Binance OI fetch failed for %s: %s", asset.symbol, exc
                )
                points = []
        if oi_cfg.bybit and asset.binance_pair:
            try:
                bybit_points = await fetch_bybit_oi(
                    await self.client(),
                    symbol=asset.symbol,
                    pair=asset.binance_pair,
                    interval=interval,
                    start=start,
                    end=now,
                )
                points.extend(bybit_points)
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "Bybit OI fetch failed for %s: %s", asset.symbol, exc
                )
        if oi_cfg.okx:
            try:
                okx_points = await fetch_okx_oi(
                    await self.client(),
                    symbol=asset.symbol,
                    interval=interval,
                    start=start,
                    end=now,
                )
                points.extend(okx_points)
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "OKX OI fetch failed for %s: %s", asset.symbol, exc
                )
        if not points:
            return 0
        return self.store.upsert_open_interest(points)

    async def backfill_oi_all(
        self,
        *,
        interval: str = "1h",
        full: bool = False,
        top: int | None = None,
    ) -> dict[str, int]:
        if not self.store.active_assets():
            await self.update_universe()
        assets = self.store.active_assets(limit=top)
        results: dict[str, int] = {}
        for asset in assets:
            try:
                results[asset.symbol] = await self.backfill_open_interest(
                    asset, interval=interval, full=full
                )
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "OI fetch failed for %s: %s", asset.symbol, exc
                )
                results[asset.symbol] = 0
            await asyncio.sleep(self.config.open_interest.request_delay_seconds)
        return results

    async def backfill_macro(self) -> dict[str, int]:
        macro_cfg = self.config.macro
        if not macro_cfg.enabled:
            return {}
        api_key = os.environ.get("FRED_API_KEY", "").strip()
        if not api_key:
            LOGGER.warning("FRED_API_KEY not set — skipping macro sync")
            return {}
        client = await self.client()
        results: dict[str, int] = {}
        for series_id in macro_cfg.series_ids:
            try:
                points = await fetch_fred_series(
                    client,
                    series_id=series_id,
                    api_key=api_key,
                )
                count = self.store.upsert_macro_series(points)
                results[series_id] = count
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "FRED fetch failed for %s: %s", series_id, exc
                )
                results[series_id] = 0
            await asyncio.sleep(macro_cfg.request_delay_seconds)
        return results

    async def _fetch_daily(
        self,
        asset: Asset,
        *,
        start: date | None,
        end: date,
    ) -> list[DailyOhlcv]:
        client = await self.client()
        if asset.binance_pair and self.config.universe.source_preference == "binance":
            try:
                candles = await fetch_binance_daily(
                    client,
                    symbol=asset.symbol,
                    pair=asset.binance_pair,
                    start=start,
                    end=end,
                )
                if candles:
                    return candles
            except httpx.HTTPError as exc:
                LOGGER.warning("%s", _binance_fallback_msg(asset.symbol, "daily", exc))

        if asset.coinbase_product_id:
            try:
                candles = await fetch_coinbase_daily(
                    client,
                    symbol=asset.symbol,
                    product_id=asset.coinbase_product_id,
                    start=start,
                    end=end,
                )
                if candles:
                    return candles
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "Coinbase daily fetch failed for %s: %s", asset.symbol, exc
                )

        return await fetch_coingecko_daily(
            client,
            symbol=asset.symbol,
            coingecko_id=asset.coingecko_id,
            start=start,
            end=end,
        )

    async def _fetch_hourly(
        self,
        asset: Asset,
        *,
        start: datetime,
        end: datetime,
    ) -> list[HourlyOhlcv]:
        client = await self.client()
        if asset.binance_pair and self.config.universe.source_preference == "binance":
            try:
                candles = await fetch_binance_hourly(
                    client,
                    symbol=asset.symbol,
                    pair=asset.binance_pair,
                    start=start,
                    end=end,
                )
                if candles:
                    return candles
            except httpx.HTTPError as exc:
                LOGGER.warning("%s", _binance_fallback_msg(asset.symbol, "hourly", exc))
        if asset.coinbase_product_id:
            try:
                return await fetch_coinbase_hourly(
                    client,
                    symbol=asset.symbol,
                    product_id=asset.coinbase_product_id,
                    start=start,
                    end=end,
                )
            except httpx.HTTPError as exc:
                LOGGER.warning(
                    "Coinbase hourly fetch failed for %s: %s", asset.symbol, exc
                )
        return []

    async def _asset_from_market(
        self,
        item: dict[str, str | int],
        rank: int,
        as_of: date,
    ) -> Asset:
        client = await self.client()
        symbol = str(item["symbol"]).upper()
        pair = default_binance_pair(symbol)
        binance_pair = pair if await probe_binance_pair(client, pair) else None
        product_id = coinbase_product_id(symbol)
        coinbase_id = (
            product_id if await probe_coinbase_product(client, product_id) else None
        )
        return Asset(
            rank=rank,
            symbol=symbol,
            coingecko_id=str(item["coingecko_id"]),
            binance_pair=binance_pair,
            coinbase_product_id=coinbase_id,
            active=True,
            as_of_date=as_of,
        )


def _binance_fallback_msg(symbol: str, interval: str, exc: httpx.HTTPError) -> str:
    """Human-readable Binance failure; 451 is a common geo-restriction."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 451:
        return (
            f"Binance {interval} unavailable for {symbol} (HTTP 451 geo-restricted); "
            "falling back to Coinbase/CoinGecko"
        )
    return f"Binance {interval} fetch failed for {symbol}: {exc}"


def _update_state(
    state: SyncState,
    points: list[date | datetime],
    *,
    full: bool,
) -> None:
    if not points:
        return
    first = min(points)
    last = max(points)
    if state.earliest_at is None or first < state.earliest_at:
        state.earliest_at = first
    if state.latest_at is None or last > state.latest_at:
        state.latest_at = last
    state.last_refresh_at = datetime.now(tz=UTC)
    if full:
        state.backfill_complete = True
