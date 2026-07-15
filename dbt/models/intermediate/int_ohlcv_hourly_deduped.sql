{{
    config(
        materialized='view',
        schema='intermediate',
        tags=['market']
    )
}}

with ranked as (
  select
    symbol,
    hour,
    open,
    high,
    low,
    close,
    volume,
    source,
    row_number() over (
      partition by symbol, hour
      order by {{ source_priority_rank('source') }}
    ) as source_rank
  from {{ ref('stg_ohlcv_hourly') }}
)

select
  symbol,
  hour,
  open,
  high,
  low,
  close,
  volume,
  source
from ranked
where source_rank = 1
