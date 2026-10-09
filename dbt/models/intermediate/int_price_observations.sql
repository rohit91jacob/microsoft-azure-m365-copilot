-- Every price observation next to the same price point in the immediately preceding snapshot
-- of the same scope (null when the price point was absent from that snapshot). Comparing
-- consecutive snapshots only keeps the change history well defined; partial scopes (one
-- archived page) are compared key by key, so a key missing from a snapshot is never read as a
-- price that disappeared.
with snapshots as (
    select distinct
        scope,
        snapshot_ts
    from {{ ref('stg_prices') }}
),

sequenced as (
    select
        scope,
        snapshot_ts,
        lag(snapshot_ts) over (partition by scope order by snapshot_ts) as prev_snapshot_ts
    from snapshots
)

select
    p.*,
    prev.snapshot_id as prev_snapshot_id,
    s.prev_snapshot_ts,
    prev.origin as prev_origin,
    prev.retail_price as prev_retail_price,
    prev.effective_start_date as prev_effective_start_date
from {{ ref('stg_prices') }} as p
inner join sequenced as s
    on p.scope = s.scope and p.snapshot_ts = s.snapshot_ts
left join {{ ref('stg_prices') }} as prev
    on
        p.scope = prev.scope
        and p.price_key = prev.price_key
        and prev.snapshot_ts = s.prev_snapshot_ts
