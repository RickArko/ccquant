{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'hour'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['market']
    )
}}

select
  symbol,
  hour,
  open,
  high,
  low,
  close,
  volume,
  source
from {{ ref('int_ohlcv_hourly_deduped') }}
{% if is_incremental() %}
where hour >= (
  select coalesce(max(hour), cast('1970-01-01' as timestamp)) from {{ this }}
) - interval 7 day
{% endif %}
