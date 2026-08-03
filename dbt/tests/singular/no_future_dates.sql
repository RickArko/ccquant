-- Fail if any fact table contains dates ahead of the UTC calendar day.
-- OHLCV / signals are UTC-dated (exchange candle opens); comparing to the
-- session-local current_date false-fails after 19:00 America/Chicago when
-- UTC has already rolled to the next day.
{% set utc_today %}cast(timezone('UTC', current_timestamp) as date){% endset %}
select *
from (
  select symbol, date from {{ ref('fct_ohlcv_daily') }} where date > {{ utc_today }}
  union all
  select symbol, cast(timestamp as date) as date
  from {{ ref('fct_open_interest') }}
  where cast(timestamp as date) > {{ utc_today }}
  union all
  select symbol, date from {{ ref('mart_signals_daily') }} where date > {{ utc_today }}
) future_dates
