{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'date'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['market']
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
  e.has_negative_event,
  ws.smart_money_netflow_usd,
  ws.kol_buy_count,
  ws.deployer_activity_count,
  ws.cabal_alert_count,
  ws.top_wallet_accumulation_score,
  tw.tweet_mention_count,
  tw.kol_tweet_mention_count,
  tw.tweet_sentiment_net
from {{ ref('fct_ohlcv_daily') }} p
left join {{ ref('fct_open_interest_agg') }} oi_agg
  on p.symbol = oi_agg.symbol
  and cast(p.date as timestamp) = oi_agg.timestamp
  and oi_agg.interval = '1d'
left join {{ ref('fct_onchain_signals') }} oc
  on p.date = oc.date
left join {{ ref('fct_macro_series') }} m
  on p.date = m.date
left join {{ ref('int_events_by_date') }} e
  on p.date = e.event_date
left join {{ ref('int_symbol_chain_bridge') }} scm
  on p.symbol = scm.symbol
left join {{ ref('fct_wallet_signals_daily') }} ws
  on p.date = ws.date
  and scm.chain = ws.chain
left join {{ ref('fct_tweet_mentions_daily') }} tw
  on p.symbol = tw.symbol
  and p.date = tw.date
where p.symbol in (select symbol from {{ ref('dim_assets') }})
{% if is_incremental() %}
  and p.date >= (
    select coalesce(max(date), cast('1970-01-01' as date)) from {{ this }}
  ) - interval 7 day
{% endif %}
