{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'timestamp', 'interval'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['market']
    )
}}

select
  symbol,
  timestamp,
  interval,
  count(exchange) as exchange_count,
  sum(open_interest_usd) as total_open_interest_usd
from {{ ref('fct_open_interest') }}
{% if is_incremental() %}
where timestamp >= (
  select coalesce(max(timestamp), cast('1970-01-01' as timestamp)) from {{ this }}
) - interval 7 day
{% endif %}
group by symbol, timestamp, interval
