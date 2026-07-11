from __future__ import annotations

from datetime import date

from ccquant.models import WalletPositionDaily
from ccquant.storage import MarketStore


def snapshot_bitcoin_positions(
    store: MarketStore,
    *,
    as_of: date,
    source: str = "ledger",
) -> int:
    """Write daily BTC balance snapshots from cumulative transfer ledger."""
    rows = store.connection.execute(
        """
        with btc_transfers as (
          select
            cast(block_time as date) as day,
            case
              when direction = 'inflow' then to_address
              else from_address
            end as address,
            case
              when direction = 'inflow' then amount
              else -amount
            end as delta_btc
          from wallet_transfers
          where chain = 'bitcoin'
            and asset_mint_or_contract = 'btc'
            and cast(block_time as date) <= ?
        ),
        balances as (
          select
            address,
            sum(delta_btc) as balance
          from btc_transfers
          where address != ''
          group by 1
        )
        select address, balance
        from balances
        where balance != 0
        """,
        [as_of],
    ).fetchall()
    positions = [
        WalletPositionDaily(
            address=str(row[0]),
            chain="bitcoin",
            date=as_of,
            asset_mint="btc",
            balance=float(row[1]),
            balance_usd=None,
            source=source,
        )
        for row in rows
    ]
    return store.upsert_wallet_positions_daily(positions)
