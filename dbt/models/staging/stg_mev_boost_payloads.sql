select
  cast(slot as bigint) as slot,
  cast(block_number as bigint) as block_number,
  cast(builder_pubkey as varchar) as builder_pubkey,
  cast(proposer_fee_recipient as varchar) as proposer_fee_recipient,
  cast(value_wei as double) as value_wei,
  cast(value_eth as double) as value_eth,
  cast(relay as varchar) as relay,
  cast(date as date) as date,
  cast(source as varchar) as source
from {{ source('raw', 'mev_boost_payloads') }}
