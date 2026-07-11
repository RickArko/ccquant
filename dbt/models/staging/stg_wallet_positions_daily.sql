select
  {{ normalize_wallet_address('address', 'chain') }} as address,
  cast(chain as varchar) as chain,
  cast(date as date) as date,
  cast(asset_mint as varchar) as asset_mint,
  cast(balance as double) as balance,
  cast(balance_usd as double) as balance_usd,
  cast(source as varchar) as source
from {{ source('raw', 'wallet_positions_daily') }}
