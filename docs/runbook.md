# Runbook

## Exit codes

| Code | Meaning | Where |
|---|---|---|
| 0 | OK | everything |
| 1 | A source request failed after retries (`FetchError`) | `azm365 extract` / `run` |
| 2 | Usage error | argparse |
| 3 | An **error-level** silver data-quality check failed; gold was not rebuilt | `azm365 dq` / `run`; Functions `transform` returns HTTP 422 |
| 4 | `dbt build` failed (model or test); gold was not published | `azm365 dbt` / `run`; Functions `transform` returns HTTP 500 |

## Everyday commands

```bash
uv run azm365 status                      # what is in bronze, per source/scope/origin
uv run azm365 run                         # today's live snapshots -> ... -> gold
uv run azm365 run --skip-extract          # transform only (silver -> DQ -> dbt -> gold)
uv run azm365 extract --source wayback    # pick up new Internet Archive captures
uv run azm365 report --out site           # results site
```

`AZM365_DATA_DIR` decides where the lake, warehouse and dbt state live (default `./data`).

## Reruns, backfills and idempotency

* **Same day twice**: extractors skip any scope whose manifest for today's `snapshot_id`
  already exists, silver skips any snapshot whose bronze `sha256` it has already built, and
  dbt rebuilds the marts deterministically. Nothing changes (`tests/e2e/test_idempotency.py`).
* **A scope failed half-way** (e.g. GitHub rate limit): the bronze object and manifest are
  only written once the whole scope is fetched, so just rerun `azm365 run`; completed scopes
  are skipped.
* **Re-fetch today** (e.g. the source was corrected): `azm365 run --force` (or
  `azm365 extract --force --source <s>`). Identical payloads produce identical bytes, so silver
  only rebuilds snapshots whose content actually changed.
* **Rebuild silver from bronze** (schema change in `silver.py`):
  `azm365 silver --full-refresh && azm365 run --skip-extract`.
* **Historical backfill**: live APIs have no history. `azm365 extract --source wayback`
  lands every distinct capture listed by the Internet Archive CDX API; `--as-of <date>`
  ignores captures after that date.
* **Sampling a big scope**: `azm365 run --max-pages 2` caps Retail Prices pages per filter;
  such snapshots are marked `complete = false`, raise the `prices_complete_scope_not_truncated`
  warning and are left out of cheapest-region rankings and price indices.

## When the data-quality gate fails (exit 3)

1. `cat $AZM365_DATA_DIR/lake/ops/dq_results/latest.json` - each failed check lists up to 5
   sample violations.
2. Typical causes:
   * `roadmap_live_feed_complete`: the roadmap endpoint returned a truncated or error page
     (fewer than 500 features). Re-fetch with `azm365 extract --force --source m365_roadmap`.
   * `prices_currency_usd` / `prices_non_negative`: an API change; look at the bronze object
     named in the sample (`snapshot_id`).
   * `gh_pull_dates_ordered`: GitHub returned inconsistent timestamps; re-fetch the repo.
3. If the bronze snapshot itself is bad, re-fetch it with `--force`, or move its manifest out
   of `bronze/_manifest/...` (the object stays for forensics) and rerun.

## When dbt fails (exit 4)

`$AZM365_DATA_DIR/dbt_logs/dbt.log` has the failing node. Run a single model or test with
`uv run azm365 dbt build --select <model>+`. Mart contracts are enforced: a column type change
must be reflected in the mart's YAML.

## Source-specific notes

* **Azure Retail Prices API** answers 429 under load; the client retries with exponential
  backoff and honours `Retry-After`. `NextPageLink` includes `:443`, which is normalised away
  when keying fixtures.
* **Microsoft 365 roadmap** returns **HTTP 403** to some bare user agents (observed with
  `azm365-smoke-test`). The pipeline sends a descriptive UA from `Settings.user_agent`.
* **GitHub**: anonymous limit is 60 requests/hour per IP; set `GITHUB_TOKEN` (any read-only
  token) to get 5,000. `/stats/participation` answers 202 while GitHub computes it; the
  extractor polls up to 6 times and records `null` (snapshot `complete = false`, DQ warning
  `gh_weekly_commits_present`) if it never becomes ready.
* **Internet Archive CDX** often answers 503; retries usually succeed within a minute. In the
  scheduled refresh the Wayback step is best-effort and never fails the run.

## Scheduled refresh failed

`refresh.yml` opens (or comments on) an issue titled **"Scheduled refresh is failing"**.

1. Open the run linked in the issue; the failing step names the stage (extract, DQ = exit 3,
   dbt = exit 4).
2. Transient source errors: re-run the workflow (`workflow_dispatch`). Snapshots are per UTC
   day, so a late rerun still lands that day's snapshot only if it runs before 00:00 UTC.
3. The lake is cached under the key prefix `azm365-lake-`; if the cache was evicted (7 days
   without a run), the next run starts a fresh lake from the Internet Archive history.
   Live history before that point is lost - that is the main limitation of a cache-backed lake.
4. Close the issue after a green run.

## Local Azure emulation

* **Azurite** (pinned in `package.json`): `npm ci && npm run azurite` (Blob on
  127.0.0.1:10000), then:

  ```bash
  export AZM365_LAKE=azurite
  export AZM365_BLOB_CONNECTION_STRING="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=<documented Azurite dev key>;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
  uv run azm365 run
  ```

  Silver is mirrored to `$AZM365_DATA_DIR/silver` for DuckDB/dbt (as a Function instance
  would). `uv run pytest -m azurite` runs the same flow on the fixtures; if port 10000 is
  taken, start Azurite on another port and set `AZM365_AZURITE_CONNECTION_STRING`.
* **Functions host**: with Azure Functions Core Tools installed, copy
  `functions/local.settings.example.json` to `functions/local.settings.json` and run
  `func start` in `functions/` with the repo virtualenv active. Not exercised in CI (Core
  Tools are not installed there); `tests/unit/test_function_app.py` checks the trigger
  registrations and HTTP status mapping instead.

## Deploying to Azure (never done from this repository)

1. `terraform -chdir=infra init -backend-config=environments/dev.azurerm.tfbackend`
2. `terraform -chdir=infra apply -var-file=environments/dev.tfvars`
3. Set the repository variables listed in `.github/workflows/deploy.yml` from the outputs,
   create the `production` environment, set `AZURE_DEPLOY_ENABLED=true`, run `deploy`.

The function key for Data Factory is written to Key Vault by Terraform; rotate it by bumping
`function_key_version` in `module.data_factory`.
