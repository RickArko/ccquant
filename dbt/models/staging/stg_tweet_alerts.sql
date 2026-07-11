select
  tweet_id,
  lower(handle) as handle,
  cast(alert_type as varchar) as alert_type,
  cast(severity as varchar) as severity,
  cast(symbols as varchar) as symbols,
  cast(posted_at as timestamp) as posted_at,
  cast(alerted_at as timestamp) as alerted_at,
  cast(metadata_json as varchar) as metadata_json
from {{ source('raw', 'tweet_alerts') }}
