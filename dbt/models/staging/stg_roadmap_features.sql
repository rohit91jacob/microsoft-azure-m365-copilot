-- One row per Microsoft 365 roadmap feature per snapshot (live daily pulls + Wayback captures).
select
    snapshot_id,
    snapshot_ts,
    origin,
    scope,
    is_synthetic,
    feature_id,
    title,
    status,
    created_at,
    modified_at,
    ga_label,
    ga_month,
    preview_label,
    preview_month,
    more_info_link,
    mentions_copilot
from {{ source('silver', 'roadmap_features') }}
