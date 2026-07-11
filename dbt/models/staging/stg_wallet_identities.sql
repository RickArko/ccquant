select
  identity_id,
  display_name,
  category,
  description,
  source_url,
  active
from {{ source('raw', 'wallet_identities') }}
where active = true
