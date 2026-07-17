{{
    config(
        materialized='view',
        schema='intermediate',
        tags=['mev']
    )
}}

with cex_daily as (
  select
    symbol,
    date,
    avg(mid) as cex_mid_avg,
    arg_max(mid, timestamp) as cex_mid_last,
    avg(spread_bps) as avg_spread_bps,
    avg(bid_notional_bps_25 + ask_notional_bps_25) as avg_depth_notional_bps_25,
    count(*) as snapshot_count
  from {{ ref('int_order_book_features') }}
  group by symbol, date
)

select
  d.symbol,
  d.date,
  d.venue,
  d.price_usd as dex_price_usd,
  d.source as dex_source,
  c.cex_mid_avg,
  c.cex_mid_last,
  c.avg_spread_bps,
  c.avg_depth_notional_bps_25,
  c.snapshot_count,
  case
    when c.cex_mid_last is null or c.cex_mid_last = 0 then null
    else (c.cex_mid_last - d.price_usd) / c.cex_mid_last * 10000.0
  end as basis_bps
from {{ ref('stg_dex_price_daily') }} d
left join cex_daily c
  on d.symbol = c.symbol
  and d.date = c.date
