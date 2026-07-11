select
  lower(address) as address,
  cast(chain as varchar) as chain,
  cast(source as varchar) as source,
  cast(backfill_complete as boolean) as backfill_complete,
  cast(earliest_at as timestamp) as earliest_at,
  cast(latest_at as timestamp) as latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at
from {{ source('raw', 'wallet_sync_state') }}
