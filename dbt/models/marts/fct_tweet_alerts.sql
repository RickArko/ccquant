{{
    config(
        materialized='table',
        schema='marts',
        tags=['ops', 'social']
    )
}}

select
  tweet_id,
  handle,
  alert_type,
  severity,
  symbols,
  posted_at,
  alerted_at,
  metadata_json
from {{ ref('stg_tweet_alerts') }}
