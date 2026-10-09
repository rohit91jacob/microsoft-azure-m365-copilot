-- One row per repository from its latest snapshot: popularity, commit volume, pull-request
-- throughput over the sampled window and release cadence.
with latest as (
    select * from {{ ref('int_gh_latest') }}
),

repos as (
    select r.*
    from {{ ref('stg_gh_repos') }} as r
    inner join latest as l on r.repo = l.repo and r.snapshot_ts = l.snapshot_ts
),

commits as (
    select
        w.repo,
        sum(w.commits) as commits_52w,
        sum(case when w.week_start >= {{ dbt.dateadd('day', -84, 'w.snapshot_ts') }} then w.commits else 0 end) as commits_12w
    from {{ ref('stg_gh_commit_weeks') }} as w
    inner join latest as l on w.repo = l.repo and w.snapshot_ts = l.snapshot_ts
    group by w.repo
),

pulls as (
    select p.*
    from {{ ref('stg_gh_pulls') }} as p
    inner join latest as l on p.repo = l.repo and p.snapshot_ts = l.snapshot_ts
),

pr_window as (
    select repo, min(updated_at) as pr_window_start, max(snapshot_ts) as snapshot_ts
    from pulls
    group by repo
),

merged as (
    select
        p.repo,
        p.hours_to_merge,
        case when {{ azm365.is_true('p.author_is_bot') }} then 1 else 0 end as by_bot
    from pulls as p
    inner join pr_window as w on p.repo = w.repo
    where {{ azm365.is_true('p.is_merged') }} and p.merged_at >= w.pr_window_start
),

merged_stats as (
    select repo, count(*) as merged_prs, sum(by_bot) as merged_by_bots
    from merged
    group by repo
),

merged_median as (
    select distinct repo, {{ azm365.median_over('hours_to_merge', 'repo') }} as median_hours_to_merge
    from merged
),

releases as (
    select
        r.repo,
        r.tag_name,
        r.published_at,
        lag(r.published_at) over (partition by r.repo order by r.published_at) as prev_published_at,
        row_number() over (partition by r.repo order by r.published_at desc) as rn
    from {{ ref('stg_gh_releases') }} as r
    inner join latest as l on r.repo = l.repo and r.snapshot_ts = l.snapshot_ts
    where not {{ azm365.is_true('r.prerelease') }}
),

release_gaps as (
    select distinct
        repo,
        {{ azm365.median_over(azm365.hours_between('prev_published_at', 'published_at') ~ ' / 24.0', 'repo') }} as median_days_between_releases
    from releases
    where prev_published_at is not null
),

release_stats as (
    select
        r.repo,
        sum(case when r.published_at >= {{ dbt.dateadd('day', -365, 'l.snapshot_ts') }} then 1 else 0 end) as releases_365d,
        max(case when r.rn = 1 then r.tag_name end) as latest_release_tag,
        max(r.published_at) as latest_release_at
    from releases as r
    inner join latest as l on r.repo = l.repo
    group by r.repo
)

select
    r.repo,
    r.snapshot_ts,
    r.stars,
    r.forks,
    r.open_issues,
    r.language,
    r.license,
    cast(coalesce(c.commits_52w, 0) as {{ dbt.type_bigint() }}) as commits_52w,
    cast(coalesce(c.commits_12w, 0) as {{ dbt.type_bigint() }}) as commits_12w,
    w.pr_window_start,
    cast({{ dbt.datediff('w.pr_window_start', 'w.snapshot_ts', 'hour') }} as {{ azm365.dbl() }}) / 24.0 as pr_window_days,
    coalesce(ms.merged_prs, 0) as merged_prs_in_window,
    cast(mm.median_hours_to_merge as {{ azm365.dbl() }}) as median_hours_to_merge,
    case
        when ms.merged_prs > 0 then cast(ms.merged_by_bots as {{ azm365.dbl() }}) / ms.merged_prs
    end as bot_share_of_merged,
    cast(coalesce(rs.releases_365d, 0) as {{ dbt.type_bigint() }}) as releases_365d,
    cast(rg.median_days_between_releases as {{ azm365.dbl() }}) as median_days_between_releases,
    rs.latest_release_tag,
    rs.latest_release_at,
    r.is_synthetic
from repos as r
left join commits as c on r.repo = c.repo
left join pr_window as w on r.repo = w.repo
left join merged_stats as ms on r.repo = ms.repo
left join merged_median as mm on r.repo = mm.repo
left join release_gaps as rg on r.repo = rg.repo
left join release_stats as rs on r.repo = rs.repo
