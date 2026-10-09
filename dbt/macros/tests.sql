{# Generic tests kept in-project so the build needs no package download. #}

{% test unique_combination_of_columns(model, combination_of_columns) %}
select {{ combination_of_columns | join(', ') }}, count(*) as n_rows
from {{ model }}
group by {{ combination_of_columns | join(', ') }}
having count(*) > 1
{% endtest %}

{% test expression_is_true(model, expression, column_name=None) %}
select *
from {{ model }}
where not ({{ expression }})
{% endtest %}

{% test not_negative(model, column_name) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} < 0
{% endtest %}
