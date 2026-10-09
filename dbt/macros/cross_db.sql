{#
    The few constructs where DuckDB SQL and Fabric Warehouse T-SQL differ. Everything else in the
    models is written in the common subset (no QUALIFY, FILTER, GROUP BY ALL, :: casts, LIMIT,
    boolean literals or || concatenation), plus dbt's own cross-database macros.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}

{# 8-byte floating point type #}
{% macro dbl() -%}{{ return(adapter.dispatch('dbl', 'azm365')()) }}{%- endmacro %}
{% macro default__dbl() -%}double{%- endmacro %}
{% macro fabric__dbl() -%}float{%- endmacro %}

{# natural logarithm #}
{% macro ln(expr) -%}{{ return(adapter.dispatch('ln', 'azm365')(expr)) }}{%- endmacro %}
{% macro default__ln(expr) -%}ln({{ expr }}){%- endmacro %}
{% macro fabric__ln(expr) -%}log({{ expr }}){%- endmacro %}

{# a boolean column used as a predicate #}
{% macro is_true(col) -%}{{ return(adapter.dispatch('is_true', 'azm365')(col)) }}{%- endmacro %}
{% macro default__is_true(col) -%}{{ col }}{%- endmacro %}
{% macro fabric__is_true(col) -%}{{ col }} = 1{%- endmacro %}

{# a predicate materialised as a boolean column #}
{% macro bool_expr(condition) -%}{{ return(adapter.dispatch('bool_expr', 'azm365')(condition)) }}{%- endmacro %}
{% macro default__bool_expr(condition) -%}(case when {{ condition }} then true else false end){%- endmacro %}
{% macro fabric__bool_expr(condition) -%}cast(case when {{ condition }} then 1 else 0 end as bit){%- endmacro %}

{# median of expr over a window partition (use with select distinct / max() to collapse) #}
{% macro median_over(expr, partition_by) -%}{{ return(adapter.dispatch('median_over', 'azm365')(expr, partition_by)) }}{%- endmacro %}
{% macro default__median_over(expr, partition_by) -%}
    median({{ expr }}) over (partition by {{ partition_by }})
{%- endmacro %}
{% macro fabric__median_over(expr, partition_by) -%}
    percentile_cont(0.5) within group (order by {{ expr }}) over (partition by {{ partition_by }})
{%- endmacro %}

{# timestamp -> date #}
{% macro to_date(expr) -%}{{ return(adapter.dispatch('to_date', 'azm365')(expr)) }}{%- endmacro %}
{% macro default__to_date(expr) -%}cast({{ expr }} as date){%- endmacro %}

{# hours between two timestamps, fractional #}
{% macro hours_between(start_ts, end_ts) -%}
    cast({{ dbt.datediff(start_ts, end_ts, 'second') }} as {{ azm365.dbl() }}) / 3600.0
{%- endmacro %}
