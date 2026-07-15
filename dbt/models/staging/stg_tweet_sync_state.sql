select
  lower(handle) as handle,
  cast(earliest_at as timestamp) as earliest_at,
  cast(latest_at as timestamp) as latest_at,
  cast(latest_tweet_id as varchar) as latest_tweet_id,
  cast(last_import_at as timestamp) as last_import_at,
  cast(backfill_complete as boolean) as backfill_complete
from {{ source('raw', 'tweet_sync_state') }}
