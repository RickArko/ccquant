select
  cast(metric as varchar) as metric,
  cast(source as varchar) as source,
  cast(latest_at as timestamp) as latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at
from {{ source('raw', 'onchain_sync_state') }}
