-- For every pay-as-you-go meter priced in several regions (latest complete snapshot): the
-- cheapest and the most expensive region, the median regional price and the spread.
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
        max(snapshot_ts) as snapshot_ts,
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

ranked as (
    select
        base.*,
        count(*) over (
            partition by service_name, product_name, sku_name, meter_name, unit_of_measure
        ) as n_regions,
        row_number() over (
            partition by service_name, product_name, sku_name, meter_name, unit_of_measure
            order by retail_price, arm_region_name
        ) as rn_cheapest,
        row_number() over (
            partition by service_name, product_name, sku_name, meter_name, unit_of_measure
            order by retail_price desc, arm_region_name
        ) as rn_priciest,
        {{ azm365.median_over('retail_price', 'service_name, product_name, sku_name, meter_name, unit_of_measure') }} as median_price
    from base
)

select
    c.service_name,
    c.product_name,
    c.sku_name,
    c.meter_name,
    c.unit_of_measure,
    c.n_regions,
    c.arm_region_name as cheapest_region,
    c.location as cheapest_location,
    c.retail_price as cheapest_price,
    cast(c.median_price as {{ azm365.dbl() }}) as median_price,
    d.arm_region_name as priciest_region,
    d.retail_price as priciest_price,
    (d.retail_price - c.retail_price) / c.retail_price as spread_pct,
    cast((c.median_price - c.retail_price) / c.median_price as {{ azm365.dbl() }}) as cheapest_saving_vs_median_pct,
    c.snapshot_ts,
    {{ azm365.bool_expr('c.is_synthetic_int = 1') }} as is_synthetic
from ranked as c
inner join ranked as d
    on
        c.service_name = d.service_name
        and c.product_name = d.product_name
        and c.sku_name = d.sku_name
        and c.meter_name = d.meter_name
        and c.unit_of_measure = d.unit_of_measure
        and d.rn_priciest = 1
where c.rn_cheapest = 1 and c.n_regions >= {{ var('min_regions_for_ranking') }}
