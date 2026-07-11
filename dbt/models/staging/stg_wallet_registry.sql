select
  case when chain = 'bitcoin' then address else lower(address) end as address,
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
