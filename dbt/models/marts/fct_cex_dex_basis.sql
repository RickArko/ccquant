{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key=['symbol', 'date', 'venue'],
        on_schema_change='append_new_columns',
        schema='marts',
        tags=['mev']
    )
}}

select
  symbol,
  date,
  venue,
  dex_price_usd,
  dex_source,
  cex_mid_avg,
  cex_mid_last,
  avg_spread_bps,
  avg_depth_notional_bps_25,
  snapshot_count,
  basis_bps
from {{ ref('int_cex_dex_basis') }}
{% if is_incremental() %}
where date >= (
  select coalesce(max(date), cast('1970-01-01' as date)) from {{ this }}
) - interval 7 day
{% endif %}
