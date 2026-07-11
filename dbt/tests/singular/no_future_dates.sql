-- Fail if any fact table contains dates in the future.
select *
from (
  select symbol, date from {{ ref('fct_ohlcv_daily') }} where date > current_date
  union all
  select symbol, cast(timestamp as date) as date
  from {{ ref('fct_open_interest') }}
  where cast(timestamp as date) > current_date
  union all
  select symbol, date from {{ ref('mart_signals_daily') }} where date > current_date
) future_dates
