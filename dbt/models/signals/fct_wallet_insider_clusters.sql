{{
    config(
        materialized='table',
        schema='signals',
        tags=['wallet', 'bitcoin']
    )
}}

with links as (
  select
    l.address,
    l.chain,
    l.identity_id
  from {{ ref('stg_wallet_identity_links') }} l
),

outflows as (
  select
    cast(t.block_time as date) as date,
    t.chain,
    t.tx_hash,
    t.block_time,
    t.from_address as address,
    t.to_address as counterparty,
    l.identity_id,
    t.amount
  from {{ ref('stg_wallet_transfers') }} t
  inner join links l
    on t.from_address = l.address
    and t.chain = l.chain
  where t.chain = 'bitcoin'
    and t.direction = 'outflow'
    and t.to_address != ''
),

shared_funder as (
  select
    date,
    chain,
    counterparty as cluster_key,
    count(distinct identity_id) as identity_count,
    count(distinct address) as wallet_count
  from outflows
  group by 1, 2, 3
  having count(distinct identity_id) >= 2
),

co_moves as (
  select
    cast(m1.block_time as date) as date,
    m1.chain,
    m1.identity_id,
    count(distinct m2.address) as co_move_wallet_count
  from {{ ref('fct_btc_insider_moves') }} m1
  inner join {{ ref('fct_btc_insider_moves') }} m2
    on m1.chain = m2.chain
    and m1.direction = 'inflow'
    and m2.direction = 'inflow'
    and m1.address != m2.address
    and m1.tx_hash != m2.tx_hash
    and abs(epoch(m1.block_time) - epoch(m2.block_time)) <= 3600
  where m1.entity_type in ('insider', 'whale')
  group by 1, 2, 3
)

select
  s.date,
  s.chain,
  s.cluster_key as identity_id,
  s.identity_count,
  s.wallet_count,
  'shared_funder' as cluster_type
from shared_funder s

union all

select
  c.date,
  c.chain,
  c.identity_id,
  1 as identity_count,
  c.co_move_wallet_count as wallet_count,
  'co_move_1h' as cluster_type
from co_moves c
where c.co_move_wallet_count >= 2
