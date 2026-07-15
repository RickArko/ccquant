{{
    config(
        materialized='table',
        schema='marts',
        tags=['ops']
    )
}}

-- Unified sync-cursor view for ops / data-quality monitoring.
-- Not joined into mart_signals_daily.

select
  cast('ohlcv' as varchar) as domain,
  symbol || ':' || "interval" as entity_key,
  cast(latest_at as varchar) as latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at,
  cast(backfill_complete as boolean) as backfill_complete
from {{ ref('stg_sync_state') }}

union all

select
  cast('macro' as varchar) as domain,
  series_id || ':' || source as entity_key,
  cast(latest_at as varchar) as latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at,
  cast(null as boolean) as backfill_complete
from {{ ref('stg_macro_sync_state') }}

union all

select
  cast('onchain' as varchar) as domain,
  metric || ':' || source as entity_key,
  cast(latest_at as varchar) as latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at,
  cast(null as boolean) as backfill_complete
from {{ ref('stg_onchain_sync_state') }}

union all

select
  cast('wallet' as varchar) as domain,
  address || ':' || chain || ':' || source as entity_key,
  cast(latest_at as varchar) as latest_at,
  cast(last_refresh_at as timestamp) as last_refresh_at,
  cast(backfill_complete as boolean) as backfill_complete
from {{ ref('stg_wallet_sync_state') }}

union all

select
  cast('twitter' as varchar) as domain,
  handle as entity_key,
  cast(latest_at as varchar) as latest_at,
  cast(last_import_at as timestamp) as last_refresh_at,
  cast(backfill_complete as boolean) as backfill_complete
from {{ ref('stg_tweet_sync_state') }}
