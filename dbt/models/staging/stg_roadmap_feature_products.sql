-- Product tags of each feature, with renamed products mapped to one canonical name
-- (e.g. 'Microsoft Purview compliance portal' -> 'Microsoft Purview'). One row per
-- (snapshot, feature, canonical product).
select distinct
    t.snapshot_id,
    t.feature_id,
    coalesce(a.product, t.tag_name) as product
from {{ source('silver', 'roadmap_feature_tags') }} as t
left join {{ ref('roadmap_product_aliases') }} as a
    on t.tag_name = a.raw_product
where t.tag_type = 'product'
