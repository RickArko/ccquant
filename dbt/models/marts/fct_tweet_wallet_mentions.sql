{{
    config(
        materialized='table',
        schema='marts',
        tags=['twitter']
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
left join {{ source('raw', 'wallet_registry') }} w
  on lower(e.entity_value) = lower(w.address)
  and w.active = true
where e.entity_type in ('sol_address', 'eth_address')
