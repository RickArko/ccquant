{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'timestamp', 'exchange'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['market']
    )
}}

select
  symbol,
  timestamp,
  date,
  hour,
  exchange,
  mid,
  best_bid,
  best_ask,
  spread_bps,
  bid_notional_bps_10,
  ask_notional_bps_10,
  bid_notional_bps_25,
  ask_notional_bps_25,
  bid_notional_bps_50,
  ask_notional_bps_50,
  imbalance_bps_25,
  total_notional_bps_25,
  depth_levels,
  last_update_id,
  fetched_at
from {{ ref('int_order_book_features') }}
{% if is_incremental() %}
where timestamp >= (
  select coalesce(max(timestamp), cast('1970-01-01' as timestamp)) from {{ this }}
) - interval 7 day
{% endif %}
