{{
    config(
        materialized='table',
        schema='signals',
        tags=['wallet']
    )
}}

with registry as (
  select * from {{ ref('stg_wallet_registry') }}
),

transfers as (
  select
    t.*,
    coalesce(r_from.entity_type, r_to.entity_type) as entity_type,
    coalesce(r_from.label, r_to.label) as wallet_label,
    coalesce(
      case when t.direction = 'inflow' then t.to_address else t.from_address end,
      t.to_address,
      t.from_address
    ) as watched_address
  from {{ ref('stg_wallet_transfers') }} t
  left join registry r_from
    on t.from_address = r_from.address
    and t.chain = r_from.chain
  left join registry r_to
    on t.to_address = r_to.address
    and t.chain = r_to.chain
  where r_from.address is not null or r_to.address is not null
)

select
  cast(block_time as date) as date,
  chain,
  entity_type,
  count(*) as transfer_count,
  sum(case when direction = 'inflow' then 1 else 0 end) as inflow_count,
  sum(case when direction = 'outflow' then 1 else 0 end) as outflow_count,
  sum(case when direction = 'inflow' then coalesce(amount_usd, amount) else 0 end)
    as inflow_amount,
  sum(case when direction = 'outflow' then coalesce(amount_usd, amount) else 0 end)
    as outflow_amount,
  sum(
    case when direction = 'inflow' then coalesce(amount_usd, amount)
         when direction = 'outflow' then -coalesce(amount_usd, amount)
         else 0 end
  ) as netflow_amount
from transfers
group by 1, 2, 3
