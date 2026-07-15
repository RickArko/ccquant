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

btc_transfers as (
  select
    cast(t.block_time as date) as date,
    t.chain,
    t.direction,
    t.amount,
    coalesce(t.amount_usd, t.amount * coalesce(p.close, 0)) as amount_usd,
    case
      when t.direction = 'inflow' then t.to_address
      else t.from_address
    end as watched_address,
    case
      when t.direction = 'inflow' then t.from_address
      else t.to_address
    end as counterparty_address
  from {{ ref('stg_wallet_transfers') }} t
  left join {{ ref('fct_ohlcv_daily') }} p
    on p.symbol = 'BTC'
    and cast(t.block_time as date) = p.date
  where t.chain = 'bitcoin'
    and t.asset_mint_or_contract = 'btc'
)

select
  t.date,
  t.watched_address as address,
  t.counterparty_address as counterparty,
  coalesce(r_counter.label, 'unknown') as counterparty_label,
  coalesce(r_counter.entity_type, 'unknown') as counterparty_entity_type,
  sum(case when t.direction = 'inflow' then t.amount else 0 end) as inflow_btc,
  sum(case when t.direction = 'outflow' then t.amount else 0 end) as outflow_btc,
  sum(
    case when t.direction = 'inflow' then t.amount_usd
         when t.direction = 'outflow' then -t.amount_usd
         else 0 end
  ) as netflow_usd
from btc_transfers t
left join registry r_counter
  on t.counterparty_address = r_counter.address
  and t.chain = r_counter.chain
where t.watched_address != ''
  and t.counterparty_address != ''
group by 1, 2, 3, 4, 5
