{{
    config(
        materialized='table',
        schema='events'
    )
}}

select
  row_number() over (order by cast(date as date)) as event_id,
  cast(date as date) as date,
  cast(category as varchar) as category,
  cast(title as varchar) as title,
  cast(description as varchar) as description,
  cast(asset_scope as varchar) as asset_scope,
  cast(anticipated_effect_direction as varchar) as anticipated_effect_direction,
  cast(magnitude as varchar) as magnitude,
  cast(confidence as varchar) as confidence,
  cast(source_url as varchar) as source_url
from {{ ref('events') }}
