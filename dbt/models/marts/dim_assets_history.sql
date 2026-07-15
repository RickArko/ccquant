{{
    config(
        materialized='table',
        schema='marts',
        tags=['market']
    )
}}

select
  symbol,
  rank,
  coingecko_id,
  binance_pair,
  coinbase_product_id,
  active,
  as_of_date,
  dbt_scd_id,
  dbt_updated_at,
  dbt_valid_from,
  dbt_valid_to
from {{ ref('snap_assets') }}
