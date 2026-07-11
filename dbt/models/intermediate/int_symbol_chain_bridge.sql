{{
    config(
        materialized='view',
        schema='intermediate',
        tags=['wallet']
    )
}}

select
  upper(symbol) as symbol,
  cast(chain as varchar) as chain
from {{ ref('symbol_chain_map') }}
