select
  tweet_id,
  entity_type,
  upper(entity_value) as entity_value
from {{ source('raw', 'tweet_entities') }}
