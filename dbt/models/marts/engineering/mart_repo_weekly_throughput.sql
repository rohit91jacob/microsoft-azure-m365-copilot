-- Weekly engineering throughput per repository from its latest snapshot: default-branch
-- commits (GitHub participation statistics), merged pull requests, median time to merge and
-- releases. Merged-PR counts are only complete for weeks on or after pr_window_start (the
-- oldest `updated_at` in the sample of recently updated closed PRs); earlier weeks are flagged.
with latest as (
    select * from {{ ref('int_gh_latest') }}
),

weeks as (
    select w.repo, w.week_start, w.commits, w.snapshot_id
    from {{ ref('stg_gh_commit_weeks') }} as w
    inner join latest as l on w.repo = l.repo and w.snapshot_ts = l.snapshot_ts
),

pulls as (
    select p.*
    from {{ ref('stg_gh_pulls') }} as p
    inner join latest as l on p.repo = l.repo and p.snapshot_ts = l.snapshot_ts
),

windows as (
    select repo, min(updated_at) as pr_window_start
    from pulls
    group by repo
),

merged as (
    select
        w.repo,
        w.week_start,
        p.pr_number,
        p.hours_to_merge,
        case when {{ azm365.is_true('p.author_is_bot') }} then 1 else 0 end as by_bot
    from weeks as w
    inner join pulls as p
        on
            w.repo = p.repo
            and {{ azm365.is_true('p.is_merged') }}
            and p.merged_at >= w.week_start
            and p.merged_at < {{ dbt.dateadd('day', 7, 'w.week_start') }}
),

merged_median as (
    select distinct
        repo,
        week_start,
        {{ azm365.median_over('hours_to_merge', 'repo, week_start') }} as median_hours_to_merge
    from merged
),

merged_weekly as (
    select repo, week_start, count(*) as merged_prs, sum(by_bot) as merged_prs_by_bots
    from merged
    group by repo, week_start
),

releases as (
    select w.repo, w.week_start, count(*) as releases_published
    from weeks as w
    inner join {{ ref('stg_gh_releases') }} as r
        on
            w.repo = r.repo
            and w.snapshot_id = r.snapshot_id
            and r.published_at >= w.week_start
            and r.published_at < {{ dbt.dateadd('day', 7, 'w.week_start') }}
    group by w.repo, w.week_start
)

select
    w.repo,
    w.week_start,
    w.commits,
    coalesce(mw.merged_prs, 0) as merged_prs,
    cast(coalesce(mw.merged_prs_by_bots, 0) as {{ dbt.type_bigint() }}) as merged_prs_by_bots,
    cast(mm.median_hours_to_merge as {{ azm365.dbl() }}) as median_hours_to_merge,
    coalesce(r.releases_published, 0) as releases_published,
    win.pr_window_start,
    {{ azm365.bool_expr('win.pr_window_start is not null and w.week_start >= win.pr_window_start') }} as pr_counts_complete
from weeks as w
left join merged_weekly as mw on w.repo = mw.repo and w.week_start = mw.week_start
left join merged_median as mm on w.repo = mm.repo and w.week_start = mm.week_start
left join releases as r on w.repo = r.repo and w.week_start = r.week_start
left join windows as win on w.repo = win.repo
