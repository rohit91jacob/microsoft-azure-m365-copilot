-- Features added to the public roadmap per month (by the roadmap's own `created` date), across
-- every snapshot ever taken, and the share that are Copilot features.
with features as (
    select
        feature_id,
        {{ azm365.to_date(dbt.date_trunc('month', 'created_at')) }} as created_month,
        case when {{ azm365.is_true('mentions_copilot') }} then 1 else 0 end as is_copilot
    from {{ ref('int_roadmap_features_first_seen') }}
    where created_at is not null
)

select
    created_month,
    count(*) as features_added,
    cast(sum(is_copilot) as {{ dbt.type_bigint() }}) as copilot_features_added,
    cast(sum(is_copilot) as {{ azm365.dbl() }}) / count(*) as copilot_share
from features
group by created_month
