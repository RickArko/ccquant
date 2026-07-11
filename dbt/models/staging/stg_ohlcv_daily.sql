select
  upper(symbol) as symbol,
  cast(date as date) as date,
  cast(open as double) as open,
  cast(high as double) as high,
  cast(low as double) as low,
  cast(close as double) as close,
  cast(volume as double) as volume,
  cast(source as varchar) as source
from {{ source('raw', 'ohlcv_daily') }}
