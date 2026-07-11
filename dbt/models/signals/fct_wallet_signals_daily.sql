{{
    config(
        materialized='table',
        schema='signals',
        tags=['wallet']
    )
}}

with flows as (
  select
    date,
    chain,
    sum(case when entity_type = 'smart_money' then netflow_amount else 0 end)
      as smart_money_netflow_usd,
    sum(case when entity_type = 'kol' and inflow_count > 0 then inflow_count else 0 end)
      as kol_buy_count,
    sum(case when entity_type = 'deployer' then transfer_count else 0 end)
      as deployer_activity_count
  from {{ ref('fct_wallet_flows_daily') }}
  group by 1, 2
),

cabals as (
  select
    date,
    chain,
    count(*) as cabal_alert_count
  from {{ ref('fct_wallet_cabal_events') }}
  group by 1, 2
),

smart as (
  select
    date,
    chain,
    avg(smart_money_score) as top_wallet_accumulation_score
  from {{ ref('fct_wallet_smart_money') }}
  group by 1, 2
)

select
  coalesce(f.date, c.date, s.date) as date,
  coalesce(f.chain, c.chain, s.chain) as chain,
  coalesce(f.smart_money_netflow_usd, 0) as smart_money_netflow_usd,
  coalesce(f.kol_buy_count, 0) as kol_buy_count,
  coalesce(f.deployer_activity_count, 0) as deployer_activity_count,
  coalesce(c.cabal_alert_count, 0) as cabal_alert_count,
  coalesce(s.top_wallet_accumulation_score, 0) as top_wallet_accumulation_score
from flows f
full outer join cabals c
  on f.date = c.date and f.chain = c.chain
full outer join smart s
  on coalesce(f.date, c.date) = s.date
  and coalesce(f.chain, c.chain) = s.chain
