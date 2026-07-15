-- Fail if volume or open interest USD is negative.
select *
from (
  select symbol, date, volume as metric_value, 'volume' as metric_name
  from {{ ref('fct_ohlcv_daily') }}
  where volume < 0
  union all
  select symbol, cast(timestamp as date) as date, open_interest_usd, 'open_interest_usd'
  from {{ ref('fct_open_interest') }}
  where open_interest_usd < 0
  union all
  select symbol, cast(timestamp as date) as date, total_open_interest_usd, 'total_open_interest_usd'
  from {{ ref('fct_open_interest_agg') }}
  where total_open_interest_usd < 0
) negative_values
