-- When the price points in the latest complete snapshot of each scope took effect: a
-- single-snapshot view of how recently Microsoft repriced or introduced each service's meters.
with latest as (
    select
        scope,
        service_name,
        {{ azm365.to_date(dbt.date_trunc('month', 'effective_start_date')) }} as effective_month,
        arm_region_name,
        snapshot_ts,
        case when {{ azm365.is_true('is_synthetic') }} then 1 else 0 end as is_synthetic_int
    from {{ ref('int_price_latest') }}
    where {{ azm365.is_true('scope_complete') }} and effective_start_date is not null
)

select
    scope,
    service_name,
    effective_month,
    count(*) as price_points,
    count(distinct arm_region_name) as regions,
    max(snapshot_ts) as snapshot_ts,
    {{ azm365.bool_expr('max(is_synthetic_int) = 1') }} as is_synthetic
from latest
group by scope, service_name, effective_month
