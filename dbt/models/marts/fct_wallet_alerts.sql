{{
    config(
        materialized='table',
        schema='marts',
        tags=['ops', 'wallet']
    )
}}

select
  address,
  chain,
  mint_or_contract,
  action,
  severity,
  block_time,
  tx_hash,
  alerted_at,
  metadata_json
from {{ ref('stg_wallet_alerts') }}
