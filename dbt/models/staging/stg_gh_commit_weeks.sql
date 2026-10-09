select
    snapshot_id,
    snapshot_ts,
    repo,
    week_start,
    commits
from {{ source('silver', 'gh_commit_weeks') }}
