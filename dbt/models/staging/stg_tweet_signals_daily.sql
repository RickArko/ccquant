select
  cast(date as date) as date,
  upper(symbol) as symbol,
  cast(mention_count as integer) as mention_count,
  cast(kol_mention_count as integer) as kol_mention_count,
  cast(bullish_keyword_count as integer) as bullish_keyword_count,
  cast(bearish_keyword_count as integer) as bearish_keyword_count,
  cast(unique_accounts as integer) as unique_accounts
from {{ source('raw', 'tweet_signals_daily') }}
