{{
    config(
        materialized='table',
        schema='marts',
        tags=['social']
    )
}}

select
  e.tweet_id,
  e.entity_value as address,
  e.entity_type,
  t.handle,
  t.posted_at,
  w.label as wallet_label,
  w.entity_type as wallet_entity_type
from {{ ref('stg_tweet_entities') }} e
join {{ ref('stg_tweets') }} t on e.tweet_id = t.tweet_id
left join {{ ref('stg_wallet_registry') }} w
  on lower(e.entity_value) = w.address
where e.entity_type in ('sol_address', 'eth_address')
