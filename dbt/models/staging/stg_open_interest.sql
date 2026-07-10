select
  upper(symbol) as symbol,
  cast(timestamp as timestamp) as timestamp,
  cast(open_interest as double) as open_interest,
  cast(exchange as varchar) as exchange,
  cast(unit as varchar) as unit,
  cast(interval as varchar) as interval
from {{ source('raw', 'open_interest') }}
