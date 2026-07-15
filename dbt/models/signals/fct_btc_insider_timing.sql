{{
    config(
        materialized='table',
        schema='signals',
        tags=['wallet', 'bitcoin']
    )
}}

with insider_flows as (
  select
    date,
    address,
    sum(case when direction = 'inflow' then amount_usd else 0 end) as inflow_usd,
    sum(case when direction = 'outflow' then amount_usd else 0 end) as outflow_usd,
    sum(
      case when direction = 'inflow' then amount_usd
           when direction = 'outflow' then -amount_usd
           else 0 end
    ) as netflow_usd
  from {{ ref('fct_btc_insider_moves') }}
  where entity_type in ('insider', 'whale', 'smart_money')
     or identity_id is not null
  group by 1, 2
),

btc_price as (
  select
    date,
    close,
    lead(close, 7) over (order by date) / nullif(close, 0) - 1 as fwd_return_7d
  from {{ ref('fct_ohlcv_daily') }}
  where symbol = 'BTC'
),

rolling_flows as (
  select
    f.date,
    f.address,
    f.netflow_usd,
    sum(f.netflow_usd) over (
      partition by f.address
      order by f.date
      rows between 6 preceding and current row
    ) as netflow_7d
  from insider_flows f
)

select
  r.date,
  r.address,
  r.netflow_usd as insider_netflow_usd,
  r.netflow_7d,
  p.fwd_return_7d,
  case
    when r.netflow_7d > 0 and coalesce(p.fwd_return_7d, 0) > 0.05 then 1.0
    when r.netflow_7d > 0 and coalesce(p.fwd_return_7d, 0) > 0.02 then 0.7
    when r.netflow_7d < 0 and coalesce(p.fwd_return_7d, 0) < -0.02 then 0.6
    else 0.3
  end as insider_timing_score
from rolling_flows r
left join btc_price p
  on r.date = p.date
