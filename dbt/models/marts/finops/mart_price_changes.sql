-- Every retail price change between two observations of the same price point (same meter, SKU,
-- price type, reservation term, tier, region and unit) in the same scope.
select
    scope,
    price_key,
    service_name,
    product_name,
    sku_name,
    meter_name,
    arm_region_name,
    location,
    price_type,
    reservation_term,
    unit_of_measure,
    tier_minimum_units,
    prev_snapshot_ts as from_snapshot_ts,
    snapshot_ts as to_snapshot_ts,
    prev_origin as from_origin,
    origin as to_origin,
    prev_retail_price as old_price,
    retail_price as new_price,
    retail_price - prev_retail_price as price_change,
    case
        when prev_retail_price > 0 then (retail_price - prev_retail_price) / prev_retail_price
    end as pct_change,
    case when retail_price > prev_retail_price then 'increase' else 'decrease' end as direction,
    prev_effective_start_date as old_effective_start_date,
    effective_start_date as new_effective_start_date,
    is_synthetic
from {{ ref('int_price_observations') }}
where
    prev_retail_price is not null
    and abs(retail_price - prev_retail_price) > 0.0000001
