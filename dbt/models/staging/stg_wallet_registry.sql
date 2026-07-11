select
  address,
  chain,
  label,
  entity_type,
  confidence,
  source,
  discovered_at,
  active,
  metadata_json
from {{ source('raw', 'wallet_registry') }}
where active = true
