{{
    config(
        materialized='table',
        schema='marts',
        tags=['mev']
    )
}}

with depth_daily as (
  select
    symbol,
    date,
    avg(mid) as cex_mid_avg,
    avg(spread_bps) as avg_spread_bps,
    avg(total_notional_bps_25) as avg_depth_notional_bps_25,
    avg(imbalance_bps_25) as avg_imbalance_bps_25,
    count(*) as depth_snapshot_count,
    max(exchange_count) as max_exchange_count
  from {{ ref('fct_order_book_agg') }}
  group by symbol, date
),

basis as (
  select
    symbol,
    date,
    venue,
    dex_price_usd,
    cex_mid_last,
    basis_bps,
    row_number() over (
      partition by symbol, date
      order by case when venue = 'defillama' then 0 else 1 end, venue
    ) as rn
  from {{ ref('fct_cex_dex_basis') }}
),

basis_one as (
  select
    symbol,
    date,
    venue,
    dex_price_usd,
    cex_mid_last,
    basis_bps
  from basis
  where rn = 1
),

mev_daily as (
  select
    date,
    count(*) as mev_boost_payload_count,
    sum(value_eth) as mev_boost_value_eth,
    avg(value_eth) as mev_boost_avg_value_eth,
    max(value_eth) as mev_boost_max_value_eth
  from {{ ref('stg_mev_boost_payloads') }}
  group by date
),

symbols as (
  select symbol, date from depth_daily
  union
  select symbol, date from basis_one
)

select
  s.symbol,
  s.date,
  d.cex_mid_avg,
  d.avg_spread_bps,
  d.avg_depth_notional_bps_25,
  d.avg_imbalance_bps_25,
  d.depth_snapshot_count,
  d.max_exchange_count,
  b.venue as dex_venue,
  b.dex_price_usd,
  b.cex_mid_last,
  b.basis_bps,
  -- ETH PBS / block-value context is date-global (same caveat as macro)
  m.mev_boost_payload_count,
  m.mev_boost_value_eth,
  m.mev_boost_avg_value_eth,
  m.mev_boost_max_value_eth
from symbols s
left join depth_daily d
  on s.symbol = d.symbol and s.date = d.date
left join basis_one b
  on s.symbol = b.symbol and s.date = b.date
left join mev_daily m
  on s.date = m.date
