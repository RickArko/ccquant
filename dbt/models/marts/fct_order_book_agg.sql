{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'timestamp'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['market']
    )
}}

select
  symbol,
  timestamp,
  cast(timestamp as date) as date,
  count(exchange) as exchange_count,
  avg(mid) as mid,
  avg(spread_bps) as spread_bps,
  sum(bid_notional_bps_10) as bid_notional_bps_10,
  sum(ask_notional_bps_10) as ask_notional_bps_10,
  sum(bid_notional_bps_25) as bid_notional_bps_25,
  sum(ask_notional_bps_25) as ask_notional_bps_25,
  sum(bid_notional_bps_50) as bid_notional_bps_50,
  sum(ask_notional_bps_50) as ask_notional_bps_50,
  avg(imbalance_bps_25) as imbalance_bps_25,
  sum(total_notional_bps_25) as total_notional_bps_25
from {{ ref('fct_order_book_snapshots') }}
{% if is_incremental() %}
where timestamp >= (
  select coalesce(max(timestamp), cast('1970-01-01' as timestamp)) from {{ this }}
) - interval 7 day
{% endif %}
group by symbol, timestamp
