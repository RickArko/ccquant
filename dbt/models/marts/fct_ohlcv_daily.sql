{{
    config(
        materialized='table',
        schema='marts'
    )
}}

with ranked as (
  select
    symbol,
    date,
    open,
    high,
    low,
    close,
    volume,
    source,
    row_number() over (
      partition by symbol, date
      order by
        case source
          when 'binance' then 1
          when 'coinbase' then 2
          when 'coingecko' then 3
          else 4
        end
    ) as source_rank
  from {{ ref('stg_ohlcv_daily') }}
)
select
  symbol,
  date,
  open,
  high,
  low,
  close,
  volume,
  source
from ranked
where source_rank = 1
