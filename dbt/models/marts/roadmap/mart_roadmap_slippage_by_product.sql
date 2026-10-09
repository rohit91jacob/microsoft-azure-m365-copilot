-- Per product and pair of consecutive snapshots: features with a GA month in both, how many
-- slipped or were pulled in, and the mean slip. Copilot features are broken out.
with compared as (
    select
        coalesce(p.product, '(untagged)') as product,
        h.feature_id,
        h.prev_snapshot_ts as from_snapshot_ts,
        h.snapshot_ts as to_snapshot_ts,
        case when {{ azm365.is_true('h.mentions_copilot') }} then 1 else 0 end as is_copilot,
        case when {{ azm365.is_true('h.is_synthetic') }} then 1 else 0 end as is_synthetic_int,
        case when h.ga_month > h.prev_ga_month then 1 else 0 end as slipped,
        case when h.ga_month < h.prev_ga_month then 1 else 0 end as pulled_in,
        case
            when h.ga_month > h.prev_ga_month
                then {{ dbt.datediff('h.prev_ga_month', 'h.ga_month', 'month') }}
        end as months_slipped
    from {{ ref('int_roadmap_feature_history') }} as h
    left join {{ ref('stg_roadmap_feature_products') }} as p
        on h.snapshot_id = p.snapshot_id and h.feature_id = p.feature_id
    where h.prev_ga_month is not null and h.ga_month is not null
)

select
    product,
    from_snapshot_ts,
    to_snapshot_ts,
    count(distinct feature_id) as features_compared,
    cast(sum(slipped) as {{ dbt.type_bigint() }}) as features_slipped,
    cast(sum(pulled_in) as {{ dbt.type_bigint() }}) as features_pulled_in,
    cast(sum(slipped) as {{ azm365.dbl() }}) / count(*) as slip_rate,
    avg(cast(months_slipped as {{ azm365.dbl() }})) as avg_months_slipped,
    cast(sum(is_copilot) as {{ dbt.type_bigint() }}) as copilot_features_compared,
    cast(sum(case when is_copilot = 1 then slipped else 0 end) as {{ dbt.type_bigint() }}) as copilot_features_slipped,
    {{ azm365.bool_expr('max(is_synthetic_int) = 1') }} as is_synthetic
from compared
group by product, from_snapshot_ts, to_snapshot_ts
