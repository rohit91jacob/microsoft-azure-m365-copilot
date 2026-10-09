-- Published, non-draft releases only.
select
    snapshot_id,
    snapshot_ts,
    repo,
    release_id,
    tag_name,
    release_name,
    prerelease,
    published_at,
    assets
from {{ source('silver', 'gh_releases') }}
where not {{ azm365.is_true('draft') }} and published_at is not null
