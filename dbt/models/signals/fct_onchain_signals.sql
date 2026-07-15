{{
    config(
        materialized='table',
        schema='signals',
        tags=['macro']
    )
}}

select
  date,
  max(case when metric = 'hashrate' then value end) as hashrate,
  max(case when metric = 'difficulty' then value end) as difficulty,
  max(case when metric = 'miner_revenue_usd' then value end) as miner_revenue_usd,
  max(case when metric = 'fees_usd' then value end) as fees_usd,
  max(case when metric = 'active_addresses' then value end) as active_addresses,
  max(case when metric = 'tx_count' then value end) as tx_count,
  max(case when metric = 'transfer_volume_usd' then value end) as transfer_volume_usd,
  max(case when metric = 'market_cap' then value end) as market_cap,
  max(case when metric = 'supply' then value end) as supply,
  max(case when metric = 'cost_per_tx_pct' then value end) as cost_per_tx_pct,
  max(case when metric = 'mvrv' then value end) as mvrv,
  max(case when metric = 'nupl' then value end) as nupl,
  max(case when metric = 'realized_price' then value end) as realized_price
from {{ ref('stg_onchain_series') }}
group by date
