{% snapshot snap_assets %}

{{
    config(
      target_schema='snapshots',
      unique_key='symbol',
      strategy='check',
      check_cols=['rank', 'binance_pair', 'coinbase_product_id', 'active', 'coingecko_id'],
    )
}}

select
  symbol,
  rank,
  coingecko_id,
  binance_pair,
  coinbase_product_id,
  active,
  as_of_date
from {{ source('raw', 'assets') }}
where active = true
  and as_of_date = (select max(as_of_date) from {{ source('raw', 'assets') }})

{% endsnapshot %}
