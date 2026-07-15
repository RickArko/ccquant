{{
    config(
        materialized='table',
        schema='marts',
        tags=['social']
    )
}}

select
  cast(s.date as date) as date,
  s.symbol,
  s.mention_count as tweet_mention_count,
  s.kol_mention_count as kol_tweet_mention_count,
  s.bullish_keyword_count,
  s.bearish_keyword_count,
  s.unique_accounts,
  s.bullish_keyword_count - s.bearish_keyword_count as tweet_sentiment_net
from {{ ref('stg_tweet_signals_daily') }} s
where s.symbol != '_ALL'
