{{
    config(
        materialized='table',
        schema='marts'
    )
}}

select
  symbol,
  timestamp,
  interval,
  count(exchange) as exchange_count,
  sum(open_interest_usd) as total_open_interest_usd
from {{ ref('fct_open_interest') }}
group by symbol, timestamp, interval
order by symbol, timestamp
