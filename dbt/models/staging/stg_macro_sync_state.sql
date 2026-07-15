select
  cast(series_id as varchar) as series_id,
  cast(source as varchar) as source,
  cast(latest_at as timestamp) as latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at
from {{ source('raw', 'macro_sync_state') }}
