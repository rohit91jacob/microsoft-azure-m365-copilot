-- Features in the most recent roadmap snapshot, with their canonical products.
with latest as (
    select max(snapshot_ts) as snapshot_ts from {{ ref('stg_roadmap_features') }}
)

select f.*
from {{ ref('stg_roadmap_features') }} as f
inner join latest as l on f.snapshot_ts = l.snapshot_ts
