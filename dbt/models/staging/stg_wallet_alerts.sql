select
  {{ normalize_wallet_address('address', 'chain') }} as address,
  cast(chain as varchar) as chain,
  cast(mint_or_contract as varchar) as mint_or_contract,
  cast(action as varchar) as action,
  cast(severity as varchar) as severity,
  cast(block_time as timestamp) as block_time,
  cast(tx_hash as varchar) as tx_hash,
  cast(alerted_at as timestamp) as alerted_at,
  cast(metadata_json as varchar) as metadata_json
from {{ source('raw', 'wallet_alerts') }}
