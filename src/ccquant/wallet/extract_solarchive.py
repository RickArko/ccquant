from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from ccquant.models import WalletTransfer
from ccquant.wallet.normalize import transfers_from_solarchive_row

SOLARCHIVE_INDEX_URL = "https://data.solarchive.org/txs/{date}/index.json"
HF_PARTITION_API = (
    "https://huggingface.co/api/datasets/solarchive/solarchive/tree/main/txs/{date}"
)
HF_RESOLVE_URL = (
    "https://huggingface.co/datasets/solarchive/solarchive/resolve/main/{path}"
)


class SolArchivePartitionNotFoundError(RuntimeError):
    """Raised when a SolArchive partition is missing from CDN and HuggingFace."""


async def fetch_partition_index(
    client: httpx.AsyncClient,
    partition_date: date,
) -> list[str]:
    url = SOLARCHIVE_INDEX_URL.format(date=partition_date.isoformat())
    try:
        resp = await client.get(url, timeout=60.0)
        if resp.status_code == 404:
            return await _fetch_partition_index_hf(client, partition_date)
        resp.raise_for_status()
        data = resp.json()
        files = data.get("files") or data.get("parquet_files") or []
        if isinstance(files, list) and files:
            return [str(f) for f in files]
    except httpx.HTTPError:
        pass
    return await _fetch_partition_index_hf(client, partition_date)


async def _fetch_partition_index_hf(
    client: httpx.AsyncClient,
    partition_date: date,
) -> list[str]:
    url = HF_PARTITION_API.format(date=partition_date.isoformat())
    resp = await client.get(url, timeout=60.0)
    if resp.status_code == 404:
        raise SolArchivePartitionNotFoundError(
            f"No SolArchive partition for {partition_date.isoformat()}. "
            "Try a date listed at huggingface.co/datasets/solarchive/solarchive "
            "(coverage is sparse — not every calendar day exists)."
        )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise SolArchivePartitionNotFoundError(str(data["error"]))
    if not isinstance(data, list):
        return []
    parquet_paths = [
        str(item["path"])
        for item in data
        if isinstance(item, dict)
        and str(item.get("path", "")).endswith(".parquet")
    ]
    if not parquet_paths:
        raise SolArchivePartitionNotFoundError(
            f"Partition {partition_date.isoformat()} exists but has no parquet files."
        )
    return [HF_RESOLVE_URL.format(path=path) for path in parquet_paths]


async def download_parquet_file(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = await client.get(url, timeout=120.0, follow_redirects=True)
    resp.raise_for_status()
    _write_bytes(dest, resp.content)
    return dest


def _write_bytes(dest: Path, content: bytes) -> None:
    dest.write_bytes(content)


def load_transfers_from_parquet(
    parquet_path: Path,
    *,
    watched: set[str],
    conn: Any,
) -> list[WalletTransfer]:
    """Filter a SolArchive parquet partition to watched-wallet transfers."""
    watched_list = list(watched)
    if not watched_list:
        return []
    escaped = parquet_path.as_posix().replace("'", "''")
    query = f"""
        select *
        from read_parquet('{escaped}')
        where list_has_any(
            coalesce(account_keys, []),
            ?
        )
        limit 5000
    """
    try:
        rows = conn.execute(query, [watched_list]).fetchdf().to_dict("records")
    except Exception:
        rows = _load_parquet_fallback(parquet_path, watched_list, conn)
    transfers: list[WalletTransfer] = []
    for row in rows:
        transfers.extend(
            transfers_from_solarchive_row(row, watched=watched)
        )
    return transfers


def _load_parquet_fallback(
    parquet_path: Path,
    watched: list[str],
    conn: Any,
) -> list[dict[str, Any]]:
    escaped = parquet_path.as_posix().replace("'", "''")
    try:
        df = conn.execute(
            f"select * from read_parquet('{escaped}') limit 2000"
        ).fetchdf()
    except Exception:
        return []
    records = df.to_dict("records")
    filtered: list[dict[str, Any]] = []
    watched_set = set(watched)
    for row in records:
        keys = row.get("account_keys") or row.get("accounts") or []
        if isinstance(keys, str):
            keys = json.loads(keys) if keys.startswith("[") else [keys]
        if any(str(k) in watched_set for k in keys):
            filtered.append(row)
    return filtered


def partition_dates(*, days: int, end: date | None = None) -> list[date]:
    end_date = end or date.today()
    return [end_date - timedelta(days=offset) for offset in range(days)]
