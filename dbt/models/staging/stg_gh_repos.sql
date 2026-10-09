select
    snapshot_id,
    snapshot_ts,
    is_synthetic,
    repo,
    stars,
    forks,
    open_issues,
    watchers,
    language,
    license,
    default_branch,
    archived,
    created_at,
    pushed_at
from {{ source('silver', 'gh_repos') }}
