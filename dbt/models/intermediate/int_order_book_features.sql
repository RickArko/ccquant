{{
    config(
        materialized='view',
        schema='intermediate',
        tags=['market']
    )
}}

select
  symbol,
  timestamp,
  cast(timestamp as date) as date,
  date_trunc('hour', timestamp) as hour,
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
  bid_notional_bps_25 + ask_notional_bps_25 as total_notional_bps_25,
  depth_levels,
  last_update_id,
  fetched_at
from {{ ref('stg_order_book_snapshots') }}
