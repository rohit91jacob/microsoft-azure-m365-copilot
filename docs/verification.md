# Verification against independently published figures

Every number below comes from one real run: a fresh live extract on **9 October 2026**
(`azm365 run` into an empty data directory, 5 min 32 s) followed by
`scripts/verify_published.py`, which downloads a *different* published channel for each source
and compares it with the pipeline's silver/gold tables. Re-run it yourself:

```bash
uv run azm365 run
uv run python scripts/verify_published.py --json verify.json
```

Summary of that run (all checks were executed; nothing was filtered out):

| # | Our output | Published elsewhere | Result |
|---|---|---|---|
| 1 | VM pay-as-you-go hourly prices (6 sizes x Linux/Windows x every region) from the Retail Prices API | Azure pricing-calculator data that feeds [azure.microsoft.com/pricing/details/virtual-machines/linux](https://azure.microsoft.com/pricing/details/virtual-machines/linux/) (`/api/v3/pricing/virtual-machines/calculator`) | **618 / 618** region x size x OS prices equal (tolerance 0.05 %) |
| 2 | Hot LRS block blob capacity price tiers (General Block Blob v2) per region | Calculator data behind [azure.microsoft.com/pricing/details/storage/blobs](https://azure.microsoft.com/pricing/details/storage/blobs/) (`/api/v3/pricing/storage/calculator`, offer `general-purpose-v2-block-blob-flat-hot-lrs`) | **58 / 58** regions have identical tier boundaries and prices (East US: 0.0208 / 0.019968 / 0.019136 USD per GB-month at 0 / 50 TB / 500 TB) |
| 3 | Azure Functions Consumption execution-time tiers per region | Calculator data behind [azure.microsoft.com/pricing/details/functions](https://azure.microsoft.com/pricing/details/functions/) (`/api/v2/pricing/functions/calculator`, `compute-payg`) | **50 / 50** regions identical: free up to 400,000 GB-s, then USD 0.000016 per GB-s |
| 4 | Functions free grants implied by the meters' tier boundaries | Text of the Functions pricing page (fetched 9 Oct 2026): "Consumption plan pricing includes a monthly free grant of 1 million requests and 400,000 GB-s"; "Flex consumption plan pricing includes a monthly free grant of 250,000 executions and 100,000 GB-s" | **Agree**: Consumption *Standard Execution Time* tier starts at 400,000 GB-s and *Standard Total Executions* at 100,000 units of 10 (= 1,000,000); Flex *On Demand Execution Time* at 100,000 GB-s and *On Demand Total Executions* at 25,000 units of 10 (= 250,000) |
| 5 | D2s v5 Linux, East US | Linux VM pricing page / calculator | USD 0.096 per hour in both |
| 6 | Roadmap snapshot from the JSON feed: 1,902 features (746 In development, 236 Rolling out, 911 Launched, 9 Cancelled) | The official roadmap RSS feed ([`/api/v2/m365/rss`](https://www.microsoft.com/releasecommunications/api/v2/m365/rss)), fetched about an hour later | **1,902 / 1,902** feature IDs in both feeds, none in only one; status agrees for 1,902 / 1,902; GA month ("GA date: November CY2026" in the RSS description) agrees for 1,902 / 1,902 |
| 7 | Latest stable `vX.Y.Z` release of microsoft/TypeScript from the GitHub API: `v7.0.2` | npm: `typescript@latest` = 7.0.2 ([registry.npmjs.org/typescript/latest](https://registry.npmjs.org/typescript/latest)) | **Agree**, with a caveat (below) |
| 8 | Latest stable Python release of microsoft/semantic-kernel: `python-1.45.0` | PyPI: `semantic-kernel` 1.45.0 ([pypi.org/project/semantic-kernel](https://pypi.org/project/semantic-kernel/)) | **Agree** |
| 9 | Latest stable release of microsoft/vscode: `1.141.0` | Release notes page [code.visualstudio.com/updates](https://code.visualstudio.com/updates): "Visual Studio Code 1.141" | **Agree** |

## Mismatches and caveats found

* **`mart_repo_summary.latest_release_tag` is wrong for TypeScript.** The most recent release
  in microsoft/TypeScript is `vscode-typescript/v1.0.1` (1 Oct 2026), a release of a different
  artefact published from the same repository. The mart takes the newest non-prerelease of
  *any* stream, so it reports that tag; the compiler's own latest release (`v7.0.2`, matching
  npm) is only found when filtering tags to `vX.Y.Z`. semantic-kernel has the same shape
  (`dotnet-*` and `python-*` streams). `releases_365d` and `median_days_between_releases`
  therefore mix streams. This is a known limitation, not fixed: release streams are
  repository-specific and would need a per-repo tag pattern in `config/sources.yml`.
* **Reservation prices are per term, not per hour.** The Retail Prices API reports reservation
  `retailPrice` as the total for the term while `unitOfMeasure` still says `1 Hour` (D2s v5
  East US: USD 519 for 1 year, USD 997 for 3 years). The checks above use pay-as-you-go
  prices only; rankings and price indices exclude reservations; `mart_price_changes` keeps
  them, with `price_type` and `reservation_term` to tell them apart.
* **Some SQL Database eDTU meters jumped 200x-4,000x** between the June and October 2024
  Spain Central captures with the same meter, SKU and unit string (e.g. USD 0.045 -> 180). The
  pipeline reports these faithfully as price changes; they look like a re-basing of the meter
  rather than a real price rise, and nothing here can tell the two apart.
* **Price history is thin before the first live run.** The only archived history is the
  Internet Archive's captures of the *first page* (1,000 items) of the Spain Central catalogue:
  5 captures, June 2024 - February 2026. Consecutive captures share 805-876 price points; the
  rest of the page churns because page order is not stable.
* **The pricing calculator is not fully independent.** It is a different Microsoft API with a
  different shape (region slugs, graduated offers), so it catches parsing, keying, tiering and
  region-mapping errors, but both are fed by Microsoft's price list. There is no third-party
  price source to reconcile against.
* **Roadmap JSON vs RSS** are two channels of the same Microsoft system; agreement shows the
  extract is complete and correctly parsed (no paging gaps, status and GA labels intact), not
  that Microsoft's dates are accurate.
* Nothing in this repository measures Microsoft 365 or Copilot **usage**; no public source
  publishes it.

## Pipeline-internal reconciliations (run on every build)

* 18 silver data-quality checks (`src/azm365/quality.py`): 0 error-level and 0 warning-level
  failures on the 9 October 2026 data.
* `dbt build`: 24 models, 2 seeds, 74 data tests - PASS=100, WARN=0, ERROR=0 on the same data.
* `tests/e2e/test_idempotency.py`: a same-day rerun lands nothing, rebuilds nothing and
  produces byte-identical bronze and identical marts; a `--force` re-fetch and a full silver
  rebuild reproduce the same gold.
