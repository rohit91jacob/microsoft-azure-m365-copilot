-- Closed pull requests; merged ones carry their time to merge.
select
    snapshot_id,
    snapshot_ts,
    repo,
    pr_number,
    created_at,
    closed_at,
    merged_at,
    updated_at,
    base_ref,
    author_is_bot,
    author_association,
    {{ azm365.bool_expr('merged_at is not null') }} as is_merged,
    case
        when merged_at is not null then {{ azm365.hours_between('created_at', 'merged_at') }}
    end as hours_to_merge
from {{ source('silver', 'gh_pulls') }}
