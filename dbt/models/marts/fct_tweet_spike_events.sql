{{
    config(
        materialized='table',
        schema='marts',
        tags=['twitter']
    )
}}

with daily as (
  select * from {{ ref('fct_tweet_mentions_daily') }}
),
stats as (
  select
    symbol,
    avg(tweet_mention_count) as avg_mentions,
    stddev_pop(tweet_mention_count) as stdev_mentions
  from daily
  group by symbol
)
select
  d.date,
  d.symbol,
  d.tweet_mention_count as mention_count,
  s.avg_mentions,
  s.stdev_mentions,
  case
    when s.stdev_mentions > 0
      then (d.tweet_mention_count - s.avg_mentions) / s.stdev_mentions
    else 0
  end as mention_z_score
from daily d
join stats s on d.symbol = s.symbol
where s.stdev_mentions > 0
  and (d.tweet_mention_count - s.avg_mentions) / s.stdev_mentions >= 2.0
