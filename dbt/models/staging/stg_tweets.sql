select
  tweet_id,
  lower(handle) as handle,
  cast(posted_at as timestamp) as posted_at,
  text,
  lang,
  is_retweet,
  is_reply,
  reply_to_tweet_id,
  conversation_id,
  like_count,
  retweet_count,
  reply_count,
  import_source,
  imported_at
from {{ source('raw', 'tweets') }}
