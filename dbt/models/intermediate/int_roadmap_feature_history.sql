-- Each feature in each snapshot next to its previous appearance (live or archived).
select
    f.*,
    lag(snapshot_id) over (partition by feature_id order by snapshot_ts) as prev_snapshot_id,
    lag(snapshot_ts) over (partition by feature_id order by snapshot_ts) as prev_snapshot_ts,
    lag(status) over (partition by feature_id order by snapshot_ts) as prev_status,
    lag(ga_month) over (partition by feature_id order by snapshot_ts) as prev_ga_month,
    lag(ga_label) over (partition by feature_id order by snapshot_ts) as prev_ga_label,
    lag(preview_month) over (partition by feature_id order by snapshot_ts) as prev_preview_month
from {{ ref('stg_roadmap_features') }} as f
