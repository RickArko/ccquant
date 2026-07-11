{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'timestamp', 'exchange', 'interval'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['market']
    )
}}

select
  symbol,
  timestamp,
  exchange,
  interval,
  open_interest,
  unit,
  open_interest_usd
from {{ ref('int_oi_usd_normalized') }}
{% if is_incremental() %}
where timestamp >= (
  select coalesce(max(timestamp), cast('1970-01-01' as timestamp)) from {{ this }}
) - interval 7 day
{% endif %}
