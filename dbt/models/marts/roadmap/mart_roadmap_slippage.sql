-- Every change of a feature's planned GA month between two consecutive snapshots in which it
-- appears. months_moved > 0 means the date slipped later; < 0 means it was pulled in.
with products as (
    select
        snapshot_id,
        feature_id,
        {{ dbt.listagg('product', "', '", 'order by product') }} as products
    from (
        select distinct snapshot_id, feature_id, product
        from {{ ref('stg_roadmap_feature_products') }}
    ) as p
    group by snapshot_id, feature_id
)

select
    h.feature_id,
    h.title,
    pr.products,
    h.mentions_copilot,
    h.prev_snapshot_ts as from_snapshot_ts,
    h.snapshot_ts as to_snapshot_ts,
    h.prev_ga_label as from_ga_label,
    h.ga_label as to_ga_label,
    h.prev_ga_month as from_ga_month,
    h.ga_month as to_ga_month,
    {{ dbt.datediff('h.prev_ga_month', 'h.ga_month', 'month') }} as months_moved,
    case when h.ga_month > h.prev_ga_month then 'slipped' else 'pulled_in' end as direction,
    h.prev_status as from_status,
    h.status as to_status,
    h.is_synthetic
from {{ ref('int_roadmap_feature_history') }} as h
left join products as pr
    on h.snapshot_id = pr.snapshot_id and h.feature_id = pr.feature_id
where
    h.prev_ga_month is not null
    and h.ga_month is not null
    and h.ga_month <> h.prev_ga_month
