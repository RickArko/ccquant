from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from ccquant.models import WalletTransfer
from ccquant.wallet.normalize import (
    transfers_from_arbitrum_tx,
    transfers_from_solarchive_row,
)


def build_solana_bigquery_sql(
    addresses: list[str],
    *,
    start: date,
    end: date,
    limit: int = 5000,
) -> str:
    quoted = ", ".join(f"'{addr}'" for addr in addresses)
    return f"""
        select
          signature,
          block_timestamp as block_time,
          account_keys
        from `bigquery-public-data.crypto_solana_mainnet_us.transactions`
        where block_timestamp between timestamp('{start.isoformat()}')
          and timestamp('{end.isoformat()}')
          and exists (
            select 1 from unnest(account_keys) as key
            where key in ({quoted})
          )
        limit {limit}
    """


def build_arbitrum_bigquery_sql(
    addresses: list[str],
    *,
    start: date,
    end: date,
    limit: int = 5000,
) -> str:
    quoted = ", ".join(f"lower('{addr}')" for addr in addresses)
    return f"""
        select
          transaction_hash as hash,
          block_timestamp as block_time,
          from_address as `from`,
          to_address as `to`,
          value
        from `bigquery-public-data.goog_blockchain_arbitrum_one_us.transactions`
        where block_timestamp between timestamp('{start.isoformat()}')
          and timestamp('{end.isoformat()}')
          and (
            lower(from_address) in ({quoted})
            or lower(to_address) in ({quoted})
          )
        limit {limit}
    """


def run_bigquery_extract(
    sql: str,
) -> list[dict[str, Any]]:
    """Run a bounded BigQuery extract when google-cloud-bigquery is installed."""
    try:
        from google.cloud import bigquery  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-bigquery not installed; "
            "use uv sync --extra wallet or solarchive extract"
        ) from exc

    client = bigquery.Client()
    job = client.query(sql)
    return [dict(row.items()) for row in job.result()]


def rows_to_transfers(
    rows: list[dict[str, Any]],
    *,
    chain: str,
    watched: set[str],
) -> list[WalletTransfer]:
    transfers: list[WalletTransfer] = []
    for row in rows:
        if chain == "solana":
            transfers.extend(
                transfers_from_solarchive_row(row, watched=watched)
            )
        elif chain == "arbitrum":
            transfers.extend(
                transfers_from_arbitrum_tx(row, watched=watched, source="bigquery")
            )
    return transfers


def default_date_range(days: int) -> tuple[date, date]:
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=max(days - 1, 0))
    return start, end
