{% macro normalize_wallet_address(address_column, chain_column) %}
  case
    when {{ chain_column }} in (
      {%- for chain in var('evm_chains') -%}
        '{{ chain }}'{% if not loop.last %}, {% endif %}
      {%- endfor -%}
    ) then lower({{ address_column }})
    else {{ address_column }}
  end
{% endmacro %}
