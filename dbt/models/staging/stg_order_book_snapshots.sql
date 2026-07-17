select
  upper(symbol) as symbol,
  cast(timestamp as timestamp) as timestamp,
  cast(exchange as varchar) as exchange,
  cast(mid as double) as mid,
  cast(best_bid as double) as best_bid,
  cast(best_ask as double) as best_ask,
  cast(spread_bps as double) as spread_bps,
  cast(bid_notional_bps_10 as double) as bid_notional_bps_10,
  cast(ask_notional_bps_10 as double) as ask_notional_bps_10,
  cast(bid_notional_bps_25 as double) as bid_notional_bps_25,
  cast(ask_notional_bps_25 as double) as ask_notional_bps_25,
  cast(bid_notional_bps_50 as double) as bid_notional_bps_50,
  cast(ask_notional_bps_50 as double) as ask_notional_bps_50,
  cast(imbalance_bps_25 as double) as imbalance_bps_25,
  cast(depth_levels as integer) as depth_levels,
  cast(last_update_id as bigint) as last_update_id,
  cast(fetched_at as timestamp) as fetched_at
from {{ source('raw', 'order_book_snapshots') }}
