select
  upper(symbol) as symbol,
  cast(date as date) as date,
  cast(venue as varchar) as venue,
  cast(price_usd as double) as price_usd,
  cast(source as varchar) as source
from {{ source('raw', 'dex_price_daily') }}
