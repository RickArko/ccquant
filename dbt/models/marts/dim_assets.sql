{{
    config(
        materialized='table',
        schema='marts'
    )
}}

select
  a.rank,
  a.symbol,
  a.coingecko_id,
  a.binance_pair,
  a.coinbase_product_id,
  a.active,
  a.as_of_date
from {{ ref('stg_assets') }} a
inner join (
  select max(as_of_date) as latest_date
  from {{ ref('stg_assets') }}
) latest on a.as_of_date = latest.latest_date
where a.active = true
order by a.rank
