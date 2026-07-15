{{
    config(
        materialized='table',
        schema='signals',
        tags=['wallet', 'bitcoin']
    )
}}

with btc_transfers as (
  select
    t.block_time,
    cast(t.block_time as date) as date,
    case
      when t.direction = 'inflow' then t.to_address
      else t.from_address
    end as address,
    case
      when t.direction = 'inflow' then t.amount
      else -t.amount
    end as delta_btc
  from {{ ref('stg_wallet_transfers') }} t
  where t.chain = 'bitcoin'
    and t.asset_mint_or_contract = 'btc'
),

daily_delta as (
  select
    date,
    address,
    sum(delta_btc) as net_delta_btc
  from btc_transfers
  where address != ''
  group by 1, 2
),

running as (
  select
    date,
    address,
    sum(net_delta_btc) over (
      partition by address
      order by date
      rows between unbounded preceding and current row
    ) as balance_btc
  from daily_delta
)

select
  r.date,
  r.address,
  r.balance_btc,
  r.balance_btc * coalesce(p.close, 0) as balance_usd
from running r
left join {{ ref('fct_ohlcv_daily') }} p
  on p.symbol = 'BTC'
  and r.date = p.date
