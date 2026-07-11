{{
    config(
        materialized='table',
        schema='signals'
    )
}}

with registry as (
  select * from {{ ref('stg_wallet_registry') }}
),

daily as (
  select
    cast(t.block_time as date) as date,
    t.chain,
    r.address,
    r.entity_type,
    count(*) as trade_count,
    sum(case when t.direction = 'inflow' then 1 else 0 end) as buy_count,
    sum(case when t.direction = 'outflow' then 1 else 0 end) as sell_count,
    sum(
      case when t.direction = 'inflow' then 1.0
           when t.direction = 'outflow' then -1.0
           else 0.0 end
    ) / nullif(count(*), 0) as directional_score
  from {{ ref('stg_wallet_transfers') }} t
  inner join registry r
    on (
      t.from_address = r.address or t.to_address = r.address
    )
    and t.chain = r.chain
  group by 1, 2, 3, 4
)

select
  date,
  chain,
  address,
  entity_type,
  trade_count,
  buy_count,
  sell_count,
  directional_score,
  case
    when directional_score >= 0.6 and trade_count >= 5 then 0.8
    when directional_score >= 0.3 and trade_count >= 3 then 0.6
    else 0.4
  end as smart_money_score
from daily
