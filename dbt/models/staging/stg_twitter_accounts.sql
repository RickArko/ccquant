select
  handle,
  user_id,
  display_name,
  entity_type,
  chains,
  symbols_watch,
  confidence,
  source,
  active,
  metadata_json
from {{ source('raw', 'twitter_accounts') }}
where active = true
