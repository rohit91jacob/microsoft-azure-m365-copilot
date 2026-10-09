-- The latest roadmap snapshot by product, status and planned GA month, with how many of those
-- features are Copilot features (title or product mentions Copilot). A feature tagged with two
-- products counts once under each.
with features as (
    select
        f.feature_id,
        f.status,
        f.ga_month,
        f.snapshot_ts,
        case when {{ azm365.is_true('f.mentions_copilot') }} then 1 else 0 end as is_copilot,
        case when {{ azm365.is_true('f.is_synthetic') }} then 1 else 0 end as is_synthetic_int,
        coalesce(p.product, '(untagged)') as product
    from {{ ref('int_roadmap_latest') }} as f
    left join {{ ref('stg_roadmap_feature_products') }} as p
        on f.snapshot_id = p.snapshot_id and f.feature_id = p.feature_id
)

select
    product,
    status,
    ga_month,
    count(distinct feature_id) as features,
    count(distinct case when is_copilot = 1 then feature_id end) as copilot_features,
    max(snapshot_ts) as snapshot_ts,
    {{ azm365.bool_expr('max(is_synthetic_int) = 1') }} as is_synthetic
from features
group by product, status, ga_month
