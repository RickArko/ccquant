{{
    config(
        materialized='view',
        schema='intermediate',
        tags=['macro']
    )
}}

select
  cast(date as date) as event_date,
  count(*) as event_count,
  max(case when anticipated_effect_direction = 'positive' then 1 else 0 end)
    as has_positive_event,
  max(case when anticipated_effect_direction = 'negative' then 1 else 0 end)
    as has_negative_event
from {{ ref('dim_events') }}
group by cast(date as date)
