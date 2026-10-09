-- One row per Azure retail price point per snapshot. Regional rows have a real ARM region;
-- 'Global' and blank regions are priced once worldwide. Rankable regions are regional rows
-- outside the sovereign, operator-edge and restricted regions listed in region_exclusions.
select
    p.snapshot_id,
    p.snapshot_ts,
    p.origin,
    p.scope,
    p.scope_complete,
    p.is_synthetic,
    p.price_key,
    p.currency_code,
    p.meter_id,
    p.meter_name,
    p.sku_id,
    p.sku_name,
    p.arm_sku_name,
    p.product_id,
    p.product_name,
    p.service_id,
    p.service_name,
    p.service_family,
    p.arm_region_name,
    p.location,
    p.price_type,
    coalesce(p.reservation_term, '') as reservation_term,
    p.unit_of_measure,
    p.tier_minimum_units,
    cast(p.retail_price as {{ azm365.dbl() }}) as retail_price,
    cast(p.unit_price as {{ azm365.dbl() }}) as unit_price,
    p.effective_start_date,
    p.is_primary_meter_region,
    {{ azm365.bool_expr("p.arm_region_name is not null and lower(p.arm_region_name) <> 'global'") }} as is_regional,
    {{ azm365.bool_expr("p.arm_region_name is not null and lower(p.arm_region_name) <> 'global' and x.arm_region_name is null") }} as is_rankable_region
from {{ source('silver', 'prices') }} as p
left join {{ ref('region_exclusions') }} as x
    on p.arm_region_name = x.arm_region_name
where p.currency_code = 'USD'
