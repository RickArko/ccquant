select
  chain,
  tx_hash,
  transfer_index,
  block_time,
  from_address,
  to_address,
  asset_mint_or_contract,
  asset_symbol,
  amount,
  amount_usd,
  direction,
  program_or_method,
  source
from {{ source('raw', 'wallet_transfers') }}
