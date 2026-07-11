{% macro normalize_wallet_address(address_column, chain_column) %}
  case
    when {{ chain_column }} in ('arbitrum', 'ethereum') then lower({{ address_column }})
    else {{ address_column }}
  end
{% endmacro %}
