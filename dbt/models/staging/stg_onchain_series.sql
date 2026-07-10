select
  cast(metric as varchar) as metric,
  cast(date as date) as date,
  cast(value as double) as value,
  cast(source as varchar) as source
from {{ source('raw', 'onchain_series') }}
