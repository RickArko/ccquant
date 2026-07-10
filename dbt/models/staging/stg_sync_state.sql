select
  upper(symbol) as symbol,
  cast(interval as varchar) as interval,
  cast(backfill_complete as boolean) as backfill_complete,
  earliest_at,
  latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at
from {{ source('raw', 'sync_state') }}
