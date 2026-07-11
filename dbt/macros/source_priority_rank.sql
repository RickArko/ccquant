{% macro source_priority_rank(source_col) %}
case {{ source_col }}
  when 'binance' then 1
  when 'coinbase' then 2
  when 'coingecko' then 3
  else 4
end
{% endmacro %}
