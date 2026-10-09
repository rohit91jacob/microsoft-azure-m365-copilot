-- Price points compared and changed between consecutive observations, per scope and pair of
-- snapshots. "Compared" only counts price points present in both snapshots.
with pairs as (
    select
        scope,
        prev_snapshot_ts as from_snapshot_ts,
        snapshot_ts as to_snapshot_ts,
        prev_origin as from_origin,
        origin as to_origin,
        case when {{ azm365.is_true('is_synthetic') }} then 1 else 0 end as is_synthetic_int,
        retail_price,
        prev_retail_price,
        case
            when abs(retail_price - prev_retail_price) > 0.0000001 then 1 else 0
        end as is_changed,
        case
            when abs(retail_price - prev_retail_price) > 0.0000001 and prev_retail_price > 0
                then (retail_price - prev_retail_price) / prev_retail_price
        end as pct_change
    from {{ ref('int_price_observations') }}
    where prev_retail_price is not null
),

medians as (
    select distinct
        scope,
        from_snapshot_ts,
        to_snapshot_ts,
        {{ azm365.median_over('pct_change', 'scope, from_snapshot_ts, to_snapshot_ts') }} as median_pct_change_of_changed
    from pairs
    where pct_change is not null
)

select
    p.scope,
    p.from_snapshot_ts,
    p.to_snapshot_ts,
    p.from_origin,
    p.to_origin,
    count(*) as price_points_compared,
    cast(sum(p.is_changed) as {{ dbt.type_bigint() }}) as price_points_changed,
    cast(sum(case when p.is_changed = 1 and p.retail_price > p.prev_retail_price then 1 else 0 end) as {{ dbt.type_bigint() }}) as increases,
    cast(sum(case when p.is_changed = 1 and p.retail_price < p.prev_retail_price then 1 else 0 end) as {{ dbt.type_bigint() }}) as decreases,
    cast(sum(p.is_changed) as {{ azm365.dbl() }}) / count(*) as changed_share,
    cast(max(m.median_pct_change_of_changed) as {{ azm365.dbl() }}) as median_pct_change_of_changed,
    {{ azm365.bool_expr('max(p.is_synthetic_int) = 1') }} as is_synthetic
from pairs as p
left join medians as m
    on
        p.scope = m.scope
        and p.from_snapshot_ts = m.from_snapshot_ts
        and p.to_snapshot_ts = m.to_snapshot_ts
group by p.scope, p.from_snapshot_ts, p.to_snapshot_ts, p.from_origin, p.to_origin
