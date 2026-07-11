select
  cast(date as date) as date,
  cast(chain as varchar) as chain,
  cast(smart_money_netflow_usd as double) as smart_money_netflow_usd,
  cast(kol_buy_count as integer) as kol_buy_count,
  cast(deployer_activity_count as integer) as deployer_activity_count,
  cast(cabal_alert_count as integer) as cabal_alert_count,
  cast(top_wallet_accumulation_score as double) as top_wallet_accumulation_score
from {{ source('raw', 'wallet_signals_daily') }}
