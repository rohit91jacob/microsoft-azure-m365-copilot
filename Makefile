# Developer entrypoints. `make help` lists targets. Settings come from .env when present.
-include .env
export

UV ?= uv
FIXTURES := tests/fixtures/http
DAY1 := 2026-01-05
DAY2 := 2026-01-06
FIXTURE_DATA ?= data/fixtures

.DEFAULT_GOAL := help
.PHONY: help install lint format test test-unit test-e2e test-azurite test-live fixtures fixtures-check \
        run run-sample transform demo dq dbt-build dbt-docs dbt-fabric-compile report status azurite \
        functions-bundle tf-fmt tf-validate tf-lint tf-checkov infra-check precommit clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

install: ## Create the virtualenv (dev + fabric groups) and the pinned Azurite
	$(UV) sync --frozen --all-groups
	npm ci --no-fund --no-audit

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-fix lint and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test-unit: ## Fast unit tests (offline)
	$(UV) run pytest tests/unit

test-e2e: ## End-to-end + rerun/idempotency tests on the simulated fixtures (runs dbt)
	$(UV) run pytest -m e2e tests/e2e

test-azurite: ## Blob lake + whole pipeline against Azurite (start it with `make azurite`)
	$(UV) run pytest -m azurite tests/integration

test-live: ## Opt-in smoke test against the real public APIs
	$(UV) run pytest -m network tests/integration

test: ## Everything offline (unit + e2e; Azurite tests skip if it is not running)
	$(UV) run pytest --cov --cov-report=term

fixtures: ## Regenerate the SIMULATED HTTP fixtures (after changing config/sources.yml)
	$(UV) run python scripts/make_fixtures.py

fixtures-check: ## Fail if the committed fixtures are stale
	$(UV) run python scripts/make_fixtures.py --check

run: ## Live: extract today's snapshots -> silver -> DQ -> dbt build -> gold
	$(UV) run azm365 run

run-sample: ## Live but sampled: at most 2 Retail Prices pages per filter (marked incomplete)
	$(UV) run azm365 run --max-pages 2

transform: ## silver -> DQ -> dbt -> gold on whatever bronze already holds
	$(UV) run azm365 run --skip-extract

demo: ## Two simulated days end to end into $(FIXTURE_DATA), then the site (SIMULATED data)
	AZM365_DATA_DIR=$(FIXTURE_DATA) $(UV) run azm365 run --fixtures $(FIXTURES) --as-of $(DAY1)
	AZM365_DATA_DIR=$(FIXTURE_DATA) $(UV) run azm365 run --fixtures $(FIXTURES) --as-of $(DAY2)
	AZM365_DATA_DIR=$(FIXTURE_DATA) $(UV) run azm365 report --out site-demo

dq: ## Run the silver data-quality gate
	$(UV) run azm365 dq

dbt-build: ## dbt build (models + tests) against the current silver
	$(UV) run azm365 dbt build

dbt-docs: ## dbt docs generate (into $AZM365_DATA_DIR/dbt_target)
	$(UV) run azm365 dbt docs generate

dbt-fabric-compile: ## Compile every model as Fabric Warehouse T-SQL (no connection opened)
	cd dbt && DBT_TARGET_PATH=../data/dbt_fabric_target DBT_LOG_PATH=../data/dbt_fabric_logs \
	  $(UV) run --group fabric dbt compile --target fabric --profiles-dir . --no-populate-cache

report: ## Write the static results site to ./site (open site/index.html)
	$(UV) run azm365 report --out site

status: ## Summarise bronze snapshots
	$(UV) run azm365 status

azurite: ## Start the pinned Azurite Blob emulator on 127.0.0.1:10000
	npm run azurite

functions-bundle: ## Assemble functions/bundle (code, dbt, config, wheel, pinned requirements)
	rm -rf functions/bundle && mkdir -p functions/bundle/wheels
	$(UV) build --wheel --out-dir functions/bundle/wheels
	cp functions/function_app.py functions/host.json functions/.funcignore functions/bundle/
	cp -r dbt config functions/bundle/
	rm -rf functions/bundle/dbt/target functions/bundle/dbt/logs
	$(UV) export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt > functions/bundle/requirements.txt
	echo "azure-functions==1.25.0" >> functions/bundle/requirements.txt
	ls functions/bundle/wheels/*.whl | sed 's|functions/bundle/|./|' >> functions/bundle/requirements.txt

tf-fmt: ## terraform fmt -check
	terraform -chdir=infra fmt -check -recursive

tf-validate: ## terraform init -backend=false + validate
	terraform -chdir=infra init -backend=false -input=false
	terraform -chdir=infra validate

tf-lint: ## tflint (terraform + azurerm rulesets)
	cd infra && tflint --init && tflint --recursive

tf-checkov: ## checkov on infra/ (skips are justified inline)
	uvx --from checkov==3.3.26 python -m checkov.main -d infra --framework terraform --compact --quiet

infra-check: tf-fmt tf-validate tf-lint tf-checkov ## All infrastructure checks

precommit: ## pre-commit on every file
	$(UV) run pre-commit run --all-files

clean: ## Remove caches and local build output (not data/)
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov site site-demo functions/bundle dist
	find . -name __pycache__ -type d -prune -not -path "./.venv/*" -exec rm -rf {} +
