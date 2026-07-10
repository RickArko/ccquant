select
  cast(series_id as varchar) as series_id,
  cast(date as date) as date,
  cast(value as double) as value,
  cast(source as varchar) as source
from {{ source('raw', 'macro_series') }}
