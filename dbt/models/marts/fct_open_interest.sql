{{
    config(
        materialized='table',
        schema='marts'
    )
}}

select
  oi.symbol,
  oi.timestamp,
  oi.exchange,
  oi.interval,
  oi.open_interest,
  oi.unit,
  case
    when oi.unit = 'usd_notional' then oi.open_interest
    when oi.unit = 'coin' then oi.open_interest * d.close
    when oi.unit = 'contracts' then oi.open_interest * d.close
    else oi.open_interest
  end as open_interest_usd
from {{ ref('stg_open_interest') }} oi
left join {{ ref('fct_ohlcv_daily') }} d
  on oi.symbol = d.symbol
  and cast(oi.timestamp as date) = d.date
