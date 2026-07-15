{{
    config(
        materialized='table',
        schema='signals',
        tags=['wallet', 'bitcoin']
    )
}}

with registry as (
  select * from {{ ref('stg_wallet_registry') }}
),

links as (
  select
    l.address,
    l.chain,
    l.identity_id,
    i.display_name as identity_name,
    i.category as identity_category
  from {{ ref('stg_wallet_identity_links') }} l
  left join {{ ref('stg_wallet_identities') }} i
    on l.identity_id = i.identity_id
),

btc_moves as (
  select
    t.chain,
    t.tx_hash,
    t.block_time,
    cast(t.block_time as date) as date,
    case
      when t.direction = 'inflow' then t.to_address
      else t.from_address
    end as address,
    t.direction,
    t.amount as amount_btc,
    coalesce(t.amount_usd, t.amount * coalesce(p.close, 0)) as amount_usd,
    r.entity_type,
    r.label as wallet_label,
    l.identity_id,
    l.identity_name,
    l.identity_category
  from {{ ref('stg_wallet_transfers') }} t
  inner join registry r
    on (
      (t.direction = 'inflow' and t.to_address = r.address)
      or (t.direction = 'outflow' and t.from_address = r.address)
    )
    and t.chain = r.chain
  left join links l
    on r.address = l.address
    and r.chain = l.chain
  left join {{ ref('fct_ohlcv_daily') }} p
    on p.symbol = 'BTC'
    and cast(t.block_time as date) = p.date
  where t.chain = 'bitcoin'
    and t.asset_mint_or_contract = 'btc'
)

select
  date,
  chain,
  tx_hash,
  block_time,
  address,
  direction,
  amount_btc,
  amount_usd,
  entity_type,
  wallet_label,
  identity_id,
  identity_name,
  identity_category
from btc_moves
where amount_btc >= 10.0
  or amount_usd >= 100000
  or entity_type in ('insider', 'whale', 'exchange')
