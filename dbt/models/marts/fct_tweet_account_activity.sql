{{
    config(
        materialized='table',
        schema='marts',
        tags=['twitter']
    )
}}

select
  handle,
  cast(posted_at as date) as date,
  count(*) as tweet_count,
  sum(like_count) as total_likes,
  sum(retweet_count) as total_retweets
from {{ ref('stg_tweets') }}
group by handle, cast(posted_at as date)
