-- One row per feature ever seen in any snapshot, with its creation date and latest state.
-- Features are removed from the public roadmap some time after launch, so the union of all
-- snapshots counts additions more completely than the latest snapshot alone.
with ranked as (
    select
        feature_id,
        title,
        status,
        created_at,
        mentions_copilot,
        snapshot_ts,
        row_number() over (partition by feature_id order by snapshot_ts desc) as rn,
        min(snapshot_ts) over (partition by feature_id) as first_seen_ts,
        min(created_at) over (partition by feature_id) as first_created_at
    from {{ ref('stg_roadmap_features') }}
)

select
    feature_id,
    title,
    status as latest_status,
    first_created_at as created_at,
    mentions_copilot,
    first_seen_ts,
    snapshot_ts as last_seen_ts
from ranked
where rn = 1
