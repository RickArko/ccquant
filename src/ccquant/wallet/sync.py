from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from ccquant.config import AppConfig
from ccquant.models import WalletRegistryEntry, WalletSyncState, WalletTransfer
from ccquant.storage import MarketStore
from ccquant.wallet.alerts import detect_alerts
from ccquant.wallet.discovery import fetch_flipside_labels
from ccquant.wallet.extract_bigquery import (
    build_arbitrum_bigquery_sql,
    build_solana_bigquery_sql,
    default_date_range,
    rows_to_transfers,
    run_bigquery_extract,
)
from ccquant.wallet.extract_solarchive import (
    download_parquet_file,
    fetch_partition_index,
    load_transfers_from_parquet,
    partition_dates,
)
from ccquant.wallet.seeds import load_seed_registry, resolve_seed_path
from ccquant.wallet.tail_arbitrum import tail_arbitrum_wallets
from ccquant.wallet.tail_solana import latest_block_time, tail_solana_wallets


class WalletSync:
    def __init__(self, store: MarketStore, config: AppConfig) -> None:
        self.store = store
        self.config = config
        self._client = httpx.AsyncClient()

    async def close(self) -> None:
        await self._client.aclose()

    async def sync_all(
        self,
        *,
        full: bool = False,
        tail: bool = True,
    ) -> dict[str, int]:
        cfg = self.config.wallet_tracking
        if not cfg.enabled:
            return {}
        counts: dict[str, int] = {}
        counts["registry"] = await self.load_registry()
        if full or not self._history_complete():
            counts["history"] = await self.backfill_history()
        if tail and cfg.tail.enabled:
            counts["tail"] = await self.tail_refresh()
        return counts

    async def load_registry(self) -> int:
        seed_path = resolve_seed_path(self.config.wallet_tracking.seed_file)
        entries = load_seed_registry(seed_path)
        return self.store.upsert_wallet_registry(entries)

    async def discover(
        self,
        *,
        chain: str,
        top: int = 20,
    ) -> int:
        entries = await fetch_flipside_labels(
            self._client,
            chain=chain,
            limit=top,
        )
        return self.store.upsert_wallet_registry(entries)

    async def import_extract(
        self,
        *,
        source: str,
        partition_date: date | None = None,
        parquet_path: Path | None = None,
    ) -> int:
        watched = self._watched_addresses("solana")
        if source == "solarchive":
            return await self._import_solarchive(
                partition_date=partition_date,
                parquet_path=parquet_path,
                watched=watched,
            )
        if source == "bigquery":
            return self._import_bigquery(watched=watched)
        raise ValueError(f"unsupported extract source: {source}")

    async def backfill_history(self) -> int:
        cfg = self.config.wallet_tracking
        total = 0
        if "solana" in cfg.chains:
            if cfg.history.solana_source == "bigquery":
                total += self._import_bigquery(
                    watched=self._watched_addresses("solana"),
                    chain="solana",
                )
            else:
                total += await self._backfill_solarchive()
        if "arbitrum" in cfg.chains:
            total += self._import_bigquery(
                watched=self._watched_addresses("arbitrum"),
                chain="arbitrum",
            )
        self._mark_history_complete()
        return total

    async def tail_refresh(self) -> int:
        cfg = self.config.wallet_tracking
        total = 0
        solana_addrs = self._limited_addresses("solana")
        if solana_addrs:
            transfers = await tail_solana_wallets(
                self._client,
                rpc_url=cfg.tail.solana_rpc_url,
                addresses=solana_addrs,
                delay_seconds=cfg.tail.request_delay_seconds,
            )
            total += self.store.upsert_wallet_transfers(transfers)
            self._update_sync_states(transfers, chain="solana", source="rpc_tail")
            alerts = detect_alerts(
                transfers,
                self._registry_map(),
            )
            self.store.upsert_wallet_alerts(alerts)
        arbitrum_addrs = self._limited_addresses("arbitrum")
        if arbitrum_addrs:
            transfers = await tail_arbitrum_wallets(
                self._client,
                addresses=arbitrum_addrs,
                delay_seconds=cfg.tail.request_delay_seconds,
            )
            total += self.store.upsert_wallet_transfers(transfers)
            self._update_sync_states(transfers, chain="arbitrum", source="camp_tail")
            alerts = detect_alerts(
                transfers,
                self._registry_map(),
            )
            self.store.upsert_wallet_alerts(alerts)
        return total

    async def _backfill_solarchive(self) -> int:
        watched = self._watched_addresses("solana")
        if not watched:
            return 0
        total = 0
        cache_dir = Path("data/extracts/solarchive")
        for partition in partition_dates(
            days=self.config.wallet_tracking.history.extract_days
        ):
            try:
                files = await fetch_partition_index(self._client, partition)
            except httpx.HTTPError:
                continue
            for file_url in files[:2]:
                name = Path(file_url).name
                dest = cache_dir / partition.isoformat() / name
                if not dest.exists():
                    try:
                        await download_parquet_file(
                            self._client,
                            file_url,
                            dest,
                        )
                    except httpx.HTTPError:
                        continue
                transfers = load_transfers_from_parquet(
                    dest,
                    watched=set(watched),
                    conn=self.store.connection,
                )
                total += self.store.upsert_wallet_transfers(transfers)
                self._update_sync_states(
                    transfers,
                    chain="solana",
                    source="solarchive",
                )
                await asyncio.sleep(0.25)
        return total

    async def _import_solarchive(
        self,
        *,
        partition_date: date | None,
        parquet_path: Path | None,
        watched: list[str],
    ) -> int:
        if parquet_path is not None:
            transfers = load_transfers_from_parquet(
                parquet_path,
                watched=set(watched),
                conn=self.store.connection,
            )
            count = self.store.upsert_wallet_transfers(transfers)
            self._update_sync_states(transfers, chain="solana", source="solarchive")
            return count
        if partition_date is None:
            partition_date = date.today() - timedelta(days=1)
        cache_dir = Path("data/extracts/solarchive")
        files = await fetch_partition_index(self._client, partition_date)
        total = 0
        for file_url in files[:1]:
            dest = cache_dir / partition_date.isoformat() / Path(file_url).name
            await download_parquet_file(self._client, file_url, dest)
            transfers = load_transfers_from_parquet(
                dest,
                watched=set(watched),
                conn=self.store.connection,
            )
            total += self.store.upsert_wallet_transfers(transfers)
            self._update_sync_states(transfers, chain="solana", source="solarchive")
        return total

    def _import_bigquery(
        self,
        *,
        watched: list[str],
        chain: str = "solana",
    ) -> int:
        if not watched:
            return 0
        start, end = default_date_range(
            self.config.wallet_tracking.history.extract_days
        )
        if chain == "arbitrum":
            sql = build_arbitrum_bigquery_sql(watched, start=start, end=end)
        else:
            sql = build_solana_bigquery_sql(watched, start=start, end=end)
        try:
            rows = run_bigquery_extract(sql)
        except RuntimeError:
            return 0
        transfers = rows_to_transfers(rows, chain=chain, watched=set(watched))
        count = self.store.upsert_wallet_transfers(transfers)
        self._update_sync_states(transfers, chain=chain, source="bigquery")
        return count

    def _watched_addresses(self, chain: str) -> list[str]:
        return self.store.active_wallet_addresses(chain=chain)

    def _limited_addresses(self, chain: str) -> list[str]:
        max_wallets = self.config.wallet_tracking.tail.max_wallets
        return self._watched_addresses(chain)[:max_wallets]

    def _registry_map(self) -> dict[tuple[str, str], WalletRegistryEntry]:
        return {
            (entry.address, entry.chain): entry
            for entry in self.store.active_wallet_registry()
        }

    def _history_complete(self) -> bool:
        states = self.store.wallet_sync_states()
        if not states:
            return False
        return all(state.backfill_complete for state in states)

    def _mark_history_complete(self) -> None:
        now = datetime.now(tz=UTC)
        for address, chain in (
            (entry.address, entry.chain)
            for entry in self.store.active_wallet_registry()
        ):
            state = self.store.get_wallet_sync_state(address, chain, "history")
            latest = state.latest_at if state else now
            self.store.upsert_wallet_sync_state(
                WalletSyncState(
                    address=address,
                    chain=chain,
                    source="history",
                    backfill_complete=True,
                    earliest_at=state.earliest_at if state else now,
                    latest_at=latest,
                    last_refresh_at=now,
                )
            )

    def _update_sync_states(
        self,
        transfers: list[WalletTransfer],
        *,
        chain: str,
        source: str,
    ) -> None:
        if not transfers:
            return
        now = datetime.now(tz=UTC)
        latest = latest_block_time(transfers) or now
        addresses = {
            t.from_address or t.to_address
            for t in transfers
            if t.from_address or t.to_address
        }
        for address in addresses:
            if not address:
                continue
            state = self.store.get_wallet_sync_state(address, chain, source)
            earliest = state.earliest_at if state else latest
            self.store.upsert_wallet_sync_state(
                WalletSyncState(
                    address=address,
                    chain=chain,
                    source=source,
                    backfill_complete=state.backfill_complete if state else False,
                    earliest_at=min(earliest, latest) if earliest else latest,
                    latest_at=latest,
                    last_refresh_at=now,
                )
            )
