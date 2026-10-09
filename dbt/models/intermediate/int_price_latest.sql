-- The most recent snapshot of every price scope.
with latest as (
    select scope, max(snapshot_ts) as snapshot_ts
    from {{ ref('stg_prices') }}
    group by scope
)

select p.*
from {{ ref('stg_prices') }} as p
inner join latest as l
    on p.scope = l.scope and p.snapshot_ts = l.snapshot_ts
