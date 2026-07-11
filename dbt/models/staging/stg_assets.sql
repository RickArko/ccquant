select
  cast(rank as integer) as rank,
  upper(symbol) as symbol,
  cast(coingecko_id as varchar) as coingecko_id,
  binance_pair,
  coinbase_product_id,
  cast(active as boolean) as active,
  cast(as_of_date as date) as as_of_date
from {{ source('raw', 'assets') }}
