from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from ccquant.models import WalletTransfer
from ccquant.wallet.normalize import (
    transfer_from_bitcoin_bq_row,
    transfers_from_arbitrum_tx,
    transfers_from_solarchive_row,
)


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _exclusive_end_date(end: date) -> date:
    return end + timedelta(days=1)


def _timestamp_range_predicate(
    column: str,
    *,
    start: date,
    end: date,
) -> str:
    end_exclusive = _exclusive_end_date(end).isoformat()
    return (
        f"{column} >= timestamp('{start.isoformat()}') "
        f"and {column} < timestamp('{end_exclusive}')"
    )


def build_solana_bigquery_sql(
    addresses: list[str],
    *,
    start: date,
    end: date,
    limit: int = 5000,
) -> str:
    quoted = ", ".join(_sql_quote(addr) for addr in addresses)
    time_filter = _timestamp_range_predicate("block_timestamp", start=start, end=end)
    return f"""
        select
          signature,
          block_timestamp as block_time,
          account_keys
        from `bigquery-public-data.crypto_solana_mainnet_us.transactions`
        where {time_filter}
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
    quoted = ", ".join(f"lower({_sql_quote(addr)})" for addr in addresses)
    time_filter = _timestamp_range_predicate("block_timestamp", start=start, end=end)
    return f"""
        select
          transaction_hash as hash,
          block_timestamp as block_time,
          from_address as `from`,
          to_address as `to`,
          value
        from `bigquery-public-data.goog_blockchain_arbitrum_one_us.transactions`
        where {time_filter}
          and (
            lower(from_address) in ({quoted})
            or lower(to_address) in ({quoted})
          )
        limit {limit}
    """


def build_bitcoin_bigquery_sql(
    addresses: list[str],
    *,
    start: date,
    end: date,
    limit: int = 5000,
) -> str:
    quoted = ", ".join(_sql_quote(addr) for addr in addresses)
    time_filter = _timestamp_range_predicate("t.block_timestamp", start=start, end=end)
    return f"""
        with watched as (
          select address from unnest([{quoted}]) as address
        ),
        output_legs as (
          select
            t.hash,
            t.block_timestamp as block_time,
            output.index as leg_index,
            addr as address,
            output.value as value_sats,
            output.type as script_type,
            'inflow' as direction,
            (
              select inp_addr
              from unnest(t.inputs) as input
              cross join unnest(input.addresses) as inp_addr
              where inp_addr not in (select address from watched)
              limit 1
            ) as counterparty
          from `bigquery-public-data.crypto_bitcoin.transactions` t,
          unnest(t.outputs) as output with offset as output_offset,
          unnest(output.addresses) as addr
          where {time_filter}
            and addr in (select address from watched)
        ),
        input_legs as (
          select
            t.hash,
            t.block_timestamp as block_time,
            input.index as leg_index,
            addr as address,
            input.value as value_sats,
            input.type as script_type,
            'outflow' as direction,
            (
              select out_addr
              from unnest(t.outputs) as output
              cross join unnest(output.addresses) as out_addr
              where out_addr not in (select address from watched)
              limit 1
            ) as counterparty
          from `bigquery-public-data.crypto_bitcoin.transactions` t,
          unnest(t.inputs) as input with offset as input_offset,
          unnest(input.addresses) as addr
          where {time_filter}
            and addr in (select address from watched)
        ),
        legs as (
          select * from output_legs
          union all
          select * from input_legs
        )
        select
          hash,
          block_time,
          leg_index,
          address,
          value_sats,
          script_type,
          direction,
          counterparty
        from legs
        order by block_time desc
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
        elif chain == "bitcoin":
            transfer = transfer_from_bitcoin_bq_row(row, source="bigquery")
            if transfer is not None:
                watched_addr = transfer.to_address or transfer.from_address
                if watched_addr in watched:
                    transfers.append(transfer)
    return transfers


def default_date_range(days: int) -> tuple[date, date]:
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=max(days - 1, 0))
    return start, end
