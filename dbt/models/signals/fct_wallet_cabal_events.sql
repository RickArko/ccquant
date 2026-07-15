{{
    config(
        materialized='table',
        schema='signals',
        tags=['wallet']
    )
}}

with registry as (
  select * from {{ ref('stg_wallet_registry') }}
),

inflows as (
  select
    t.chain,
    t.asset_mint_or_contract as mint,
    t.block_time,
    coalesce(r_to.address, t.to_address) as address,
    r_to.entity_type
  from {{ ref('stg_wallet_transfers') }} t
  inner join registry r_to
    on t.to_address = r_to.address
    and t.chain = r_to.chain
  where t.direction = 'inflow'
),

windowed as (
  select
    *,
    count(*) over (
      partition by chain, mint, date_trunc('minute', block_time)
    ) as wallets_in_window
  from inflows
)

select
  cast(block_time as date) as date,
  chain,
  mint,
  date_trunc('minute', block_time) as window_start,
  wallets_in_window as wallet_count
from windowed
where wallets_in_window >= 2
group by 1, 2, 3, 4, 5
