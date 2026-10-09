-- How expensive each region is, per service: the geometric mean of (regional price / reference
-- region price) over every pay-as-you-go meter priced in both. 1.00 = same as the reference
-- region (eastus by default); rank 1 = the cheapest region for that service (ties share a rank).
with base as (
    select
        service_name,
        product_name,
        sku_name,
        meter_name,
        unit_of_measure,
        arm_region_name,
        max(location) as location,
        min(retail_price) as retail_price,
        max(case when {{ azm365.is_true('is_synthetic') }} then 1 else 0 end) as is_synthetic_int
    from {{ ref('int_price_latest') }}
    where
        {{ azm365.is_true('is_rankable_region') }}
        and {{ azm365.is_true('scope_complete') }}
        and {{ azm365.is_true('is_primary_meter_region') }}
        and price_type = 'Consumption'
        and tier_minimum_units = 0
        and retail_price > 0
    group by service_name, product_name, sku_name, meter_name, unit_of_measure, arm_region_name
),

reference_prices as (
    select * from base where arm_region_name = '{{ var("reference_region") }}'
),

ratios as (
    select
        b.service_name,
        b.arm_region_name,
        b.location,
        b.is_synthetic_int,
        {{ azm365.ln('b.retail_price / r.retail_price') }} as log_ratio
    from base as b
    inner join reference_prices as r
        on
            b.service_name = r.service_name
            and b.product_name = r.product_name
            and b.sku_name = r.sku_name
            and b.meter_name = r.meter_name
            and b.unit_of_measure = r.unit_of_measure
),

indexed as (
    select
        service_name,
        arm_region_name,
        max(location) as location,
        count(*) as meters_compared,
        round(exp(avg(log_ratio)), 6) as price_index,
        max(is_synthetic_int) as is_synthetic_int
    from ratios
    group by service_name, arm_region_name
)

select
    service_name,
    arm_region_name,
    location,
    meters_compared,
    price_index,
    '{{ var("reference_region") }}' as reference_region,
    rank() over (partition by service_name order by price_index) as rank_in_service,
    count(*) over (partition by service_name) as regions_in_service,
    {{ azm365.bool_expr('is_synthetic_int = 1') }} as is_synthetic
from indexed
