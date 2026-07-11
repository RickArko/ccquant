select
  address,
  chain,
  identity_id,
  link_type,
  confidence,
  source,
  linked_at
from {{ source('raw', 'wallet_identity_links') }}
