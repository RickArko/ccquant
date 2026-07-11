{{
    config(
        materialized='table',
        schema='marts'
    )
}}

select
  p.symbol,
  p.date,
  p.open,
  p.high,
  p.low,
  p.close,
  p.volume,
  p.source as price_source,
  oi_agg.total_open_interest_usd,
  oi_agg.exchange_count as oi_exchange_count,
  oc.hashrate,
  oc.difficulty,
  oc.miner_revenue_usd,
  oc.fees_usd,
  oc.active_addresses,
  oc.tx_count,
  oc.transfer_volume_usd,
  oc.market_cap,
  oc.supply,
  oc.mvrv,
  oc.nupl,
  oc.realized_price,
  m.m2sl,
  m.walcl,
  m.dgs10,
  m.dgs2,
  m.t10yie,
  m.fedfunds,
  m.dtwexbgs,
  m.vixcls,
  e.event_count,
  e.has_positive_event,
  e.has_negative_event
from {{ ref('fct_ohlcv_daily') }} p
left join {{ ref('fct_open_interest_agg') }} oi_agg
  on p.symbol = oi_agg.symbol
  and cast(p.date as timestamp) = oi_agg.timestamp
  and oi_agg.interval = '1d'
left join {{ ref('fct_onchain_signals') }} oc
  on p.date = oc.date
left join {{ ref('fct_macro_series') }} m
  on p.date = m.date
left join (
  select
    cast(date as date) as event_date,
    count(*) as event_count,
    max(case when anticipated_effect_direction = 'positive' then 1 else 0 end)
      as has_positive_event,
    max(case when anticipated_effect_direction = 'negative' then 1 else 0 end)
      as has_negative_event
  from {{ ref('dim_events') }}
  group by cast(date as date)
) e on p.date = e.event_date
where p.symbol in (select symbol from {{ ref('dim_assets') }})
order by p.symbol, p.date
