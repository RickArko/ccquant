{% macro pivot_metrics(relation, date_col, metric_col, value_col, metrics) %}
select
  {{ date_col }},
  {% for metric in metrics %}
  max(case when {{ metric_col }} = '{{ metric.id }}' then {{ value_col }} end)
    as {{ metric.alias }}{% if not loop.last %},{% endif %}
  {% endfor %}
from {{ relation }}
group by {{ date_col }}
{% endmacro %}
