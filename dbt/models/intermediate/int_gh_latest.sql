-- The latest GitHub snapshot of each repository.
select repo, max(snapshot_ts) as snapshot_ts
from {{ ref('stg_gh_repos') }}
group by repo
