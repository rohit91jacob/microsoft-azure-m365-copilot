# Data dictionary

All timestamps are UTC. Every silver row carries the lineage columns below; every mart carries
`is_synthetic` (true only for the simulated test fixtures).

## Lake layout (containers `bronze`, `silver`, `gold`, `ops`)

| Container | Path | Grain / content |
|---|---|---|
| bronze | `<source>/<dataset>/<scope>/snapshot_date=YYYY-MM-DD/<snapshot_id>.json.gz` | One immutable gzip'd JSON envelope per snapshot: `source, dataset, scope, snapshot_id, origin, synthetic, request, payload` (raw API payload; GitHub payloads already minimised) |
| bronze | `_manifest/<source>/<dataset>/<scope>/<snapshot_id>.json` | Manifest record: path, `sha256`, bytes, `item_count`, `complete`, `synthetic`, `fetched_at`, request. Written after the object, so a manifest always points at a complete object |
| silver | `<table>/<scope>/<snapshot_id>.parquet` | Typed Parquet (zstd), one file per table x bronze snapshot |
| silver | `_state/<source>/<dataset>/<scope>/<snapshot_id>.json` | The bronze `sha256` each silver file was built from (incremental builds) |
| gold | `<mart>/<mart>.parquet` | Every dbt mart, copied after `dbt build` succeeds |
| ops | `dq_results/<run_id>.json`, `dq_results/latest.json` | Data-quality results per run |
| ops | `runs/latest_gold.json` | Row counts of the last gold publish |

`snapshot_id` = `YYYYMMDDTHHMMSSZ-<origin>`. `origin` is `live` (stamped at 00:00 UTC of the
day it was taken, so one per scope per day) or `wayback` (stamped with the Internet Archive
capture time).

### Lineage columns (every silver table)

| Column | Type | Meaning |
|---|---|---|
| snapshot_id | varchar | Bronze snapshot the row came from |
| snapshot_ts | timestamptz | Snapshot time (UTC) |
| origin | varchar | `live` or `wayback` |
| scope | varchar | Extraction scope (`config/sources.yml`), e.g. `vm_popular_skus`, `all`, `microsoft__vscode` |
| scope_complete | boolean | False when the scope was cut short (`--max-pages`, or a single archived page) |
| is_synthetic | boolean | True only for the simulated fixtures |

## Silver tables

### prices (Azure Retail Prices API)

Grain: one row per `price_key` per snapshot and scope. `price_key` = sha1 of
`meterId|skuId|type|reservationTerm|tierMinimumUnits|armRegionName|unitOfMeasure|currencyCode`.
When one request lists two effective prices for the same key, the later `effectiveStartDate`
wins.

| Column | Source field | Notes |
|---|---|---|
| currency_code | currencyCode | Always USD (DQ error otherwise) |
| meter_id, meter_name | meterId, meterName | |
| sku_id, sku_name, arm_sku_name | skuId, skuName, armSkuName | `arm_sku_name` is null for non-VM meters |
| product_id, product_name | productId, productName | VM products ending in `Windows` are Windows; others Linux |
| service_id, service_name, service_family | serviceId, serviceName, serviceFamily | |
| arm_region_name, location | armRegionName, location | Null/`Global` for worldwide meters |
| price_type | type | `Consumption` (pay-as-you-go, includes Spot/Low Priority meters), `Reservation`, `DevTestConsumption` |
| reservation_term | reservationTerm | e.g. `1 Year`, `3 Years`; null otherwise |
| unit_of_measure | unitOfMeasure | e.g. `1 Hour`, `1 GB/Month`, `10K`, `1 GB Second`. **For reservations the price is the total for the term even though the unit says `1 Hour`** |
| tier_minimum_units | tierMinimumUnits | Lower bound of the volume tier |
| retail_price, unit_price | retailPrice, unitPrice | USD list price, no discounts |
| effective_start_date | effectiveStartDate | When this price took effect |
| is_primary_meter_region | isPrimaryMeterRegion | Rankings use primary meter regions only |

### roadmap_features (Microsoft 365 roadmap)

Grain: one row per `feature_id` per snapshot. Descriptions are not carried into silver.

| Column | Source field | Notes |
|---|---|---|
| feature_id | id | `https://www.microsoft.com/microsoft-365/roadmap?id=<feature_id>` |
| title | title | Kept in the lake for matching; not republished on the site |
| status | status | `In development`, `Rolling out`, `Launched`, `Cancelled` |
| created_at, modified_at | created, modified | |
| ga_label, ga_month | publicDisclosureAvailabilityDate | `November CY2026` -> 2026-11-01; `Q3 CY2025` -> 2025-07-01 |
| preview_label, preview_month | publicPreviewDate | Same parsing |
| more_info_link | moreInfoLink | |
| mentions_copilot | derived | Title or any product tag contains "copilot" (case-insensitive) |

### roadmap_feature_tags

Grain: (snapshot, feature, tag_type, tag_name). `tag_type` is `product`, `platform`,
`cloud_instance`, `release_phase`, or `untyped` for old captures without `tagsContainer`.

### gh_repos, gh_commit_weeks, gh_releases, gh_pulls (GitHub REST API)

| Table | Grain | Notes |
|---|---|---|
| gh_repos | repo x snapshot | stars, forks, open_issues (issues + PRs), watchers (subscribers), language, licence (SPDX), default branch, archived, created/pushed |
| gh_commit_weeks | repo x week x snapshot | `/stats/participation` "all": default-branch commits for 52 weeks. Weeks start Sunday 00:00 UTC, counted back from the snapshot |
| gh_releases | release x snapshot | First page (100) of releases: tag, name, draft, prerelease, created/published, asset count |
| gh_pulls | PR x snapshot | Up to 300 most recently *updated* closed PRs: created/closed/merged/updated, base branch, `author_is_bot`, `author_association`. No logins or text |

## Staging / intermediate (dbt)

| Model | Grain | Notes |
|---|---|---|
| stg_prices | price point x snapshot | USD only; `is_regional`, `is_rankable_region` (regional and not in seed `region_exclusions`) |
| stg_roadmap_features | feature x snapshot | |
| stg_roadmap_feature_products | snapshot x feature x canonical product | Seed `roadmap_product_aliases` maps renamed tags |
| stg_gh_* | as silver | `stg_gh_pulls.hours_to_merge`; `stg_gh_releases` drops drafts |
| int_price_observations | price point x snapshot | The same price point in the **immediately preceding snapshot of the scope** (`prev_*`, null if absent there) |
| int_price_latest | price point | Latest snapshot of each scope |
| int_roadmap_feature_history | feature x snapshot | Previous appearance (status, GA month) |
| int_roadmap_latest | feature | Latest roadmap snapshot |
| int_roadmap_features_first_seen | feature | Every feature ever seen, latest state |
| int_gh_latest | repo | Latest GitHub snapshot |

## Marts (schema `marts`, contracts enforced)

### FinOps

| Mart | Grain | Key columns |
|---|---|---|
| mart_price_changes | scope x price_key x to_snapshot | old/new price, `price_change`, `pct_change`, `direction` (increase/decrease), old/new effective dates, from/to origin |
| mart_price_change_summary | scope x consecutive snapshot pair | price points compared (present in both), changed, increases, decreases, `changed_share`, median % change of the changed |
| mart_cheapest_region | meter (service, product, SKU, meter, unit) | Latest complete snapshot, `Consumption`, tier 0, primary meter region, rankable regions, priced in >= `min_regions_for_ranking` (3) regions: cheapest/median/priciest region and price, `spread_pct`, `cheapest_saving_vs_median_pct`. Spot and Low Priority meters are separate SKUs and rank on their own |
| mart_region_price_index | service x region | Geometric mean of regional / `reference_region` (eastus) price over meters priced in both; `rank_in_service` (1 = cheapest; ties share) |
| mart_price_effective_months | scope x service x month | Price points by the month their current price took effect |

### Roadmap

| Mart | Grain | Key columns |
|---|---|---|
| mart_roadmap_cadence | canonical product x status x GA month (latest snapshot) | `features`, `copilot_features`. A feature with two product tags counts under each |
| mart_roadmap_monthly_additions | creation month | `features_added`, `copilot_features_added`, `copilot_share` over every feature ever seen |
| mart_roadmap_slippage | feature x later snapshot | Every change of planned GA month between consecutive appearances: from/to label and month, `months_moved` (> 0 slipped), `direction`, statuses, products |
| mart_roadmap_slippage_by_product | product x snapshot pair | features compared (GA month in both), slipped, pulled in, `slip_rate`, mean months slipped, Copilot breakdown |

### Engineering

| Mart | Grain | Key columns |
|---|---|---|
| mart_repo_weekly_throughput | repo x week (latest snapshot) | commits, merged PRs, bot-authored merged PRs, median hours to merge, releases; `pr_counts_complete` is false for weeks before the PR sample window |
| mart_repo_summary | repo | stars, forks, open issues, commits 52w/12w, PR window length, merged PRs and median hours to merge in the window, bot share, stable releases in 365 days, median days between releases, latest release (newest stable release of *any* stream; see verification caveat) |

## Data-quality gate (silver -> gold)

18 checks in `src/azm365/quality.py`; error-level failures stop the run with exit code 3
before dbt, warnings are recorded. dbt adds 74 data tests on sources, staging, intermediate and
marts (uniqueness, not-null, accepted values, non-negative, invariants such as
`cheapest <= median <= priciest` and `changed = increases + decreases`); a failing test fails
`dbt build` (exit code 4) and gold is not published.

| Check | Table | Severity |
|---|---|---|
| prices_not_empty, prices_key_unique, prices_non_negative, prices_required_fields, prices_currency_usd | prices | error |
| prices_price_type_known, prices_complete_scope_not_truncated, prices_effective_date_sane | prices | warn |
| roadmap_feature_unique, roadmap_live_feed_complete (>= 500 features in a real live pull), roadmap_title_present | roadmap_features | error |
| roadmap_status_known, roadmap_ga_label_parsed | roadmap_features | warn |
| roadmap_tags_reference_features | roadmap_feature_tags | error |
| gh_commit_week_unique, gh_pull_dates_ordered, gh_pull_unique | gh_* | error |
| gh_weekly_commits_present | gh_repos | warn |
