{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'date'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['market']
    )
}}

select
  symbol,
  date,
  open,
  high,
  low,
  close,
  volume,
  source
from {{ ref('int_ohlcv_daily_deduped') }}
{% if is_incremental() %}
where date >= (
  select coalesce(max(date), cast('1970-01-01' as date)) from {{ this }}
) - interval 7 day
{% endif %}
