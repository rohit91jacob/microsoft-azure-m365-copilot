"""Data-quality gate between silver and gold.

Each check is a SQL query over the silver Parquet files that returns the violating rows; the
check passes when it returns none. ``error`` checks stop the pipeline (exit code 3) before dbt
builds gold; ``warn`` checks are recorded and reported. dbt tests then gate the marts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from azm365.lake import Lake
from azm365.silver import SCHEMAS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Check:
    name: str
    table: str
    severity: str  # "error" | "warn"
    description: str
    sql: str  # SELECT returning violating rows


@dataclass
class CheckResult:
    name: str
    table: str
    severity: str
    description: str
    violations: int
    passed: bool
    sample: list[dict]


CHECKS: list[Check] = [
    # --- Azure Retail Prices --------------------------------------------------------------
    Check(
        "prices_not_empty",
        "prices",
        "error",
        "every live price snapshot landed in bronze produced at least one silver price row",
        "SELECT m.snapshot_id, m.scope, m.item_count FROM bronze_manifests m "
        "ANTI JOIN prices p ON p.snapshot_id = m.snapshot_id AND p.scope = m.scope "
        "WHERE m.source = 'azure_prices' AND m.origin = 'live'",
    ),
    Check(
        "prices_key_unique",
        "prices",
        "error",
        "price_key is unique within a snapshot and scope",
        "SELECT snapshot_id, scope, price_key, count(*) AS n FROM prices GROUP BY ALL HAVING n > 1",
    ),
    Check(
        "prices_non_negative",
        "prices",
        "error",
        "retail and unit prices are present and >= 0",
        "SELECT snapshot_id, meter_id, retail_price FROM prices "
        "WHERE retail_price IS NULL OR retail_price < 0 OR unit_price IS NULL OR unit_price < 0",
    ),
    Check(
        "prices_required_fields",
        "prices",
        "error",
        "meter, SKU, service, price type and unit are populated",
        "SELECT snapshot_id, price_key FROM prices WHERE meter_id IS NULL OR sku_id IS NULL "
        "OR service_name IS NULL OR price_type IS NULL OR unit_of_measure IS NULL",
    ),
    Check(
        "prices_currency_usd",
        "prices",
        "error",
        "all prices are in USD (the configured currency)",
        "SELECT DISTINCT snapshot_id, currency_code FROM prices WHERE currency_code <> 'USD'",
    ),
    Check(
        "prices_price_type_known",
        "prices",
        "warn",
        "price type is Consumption, Reservation or DevTestConsumption",
        "SELECT DISTINCT price_type FROM prices "
        "WHERE price_type NOT IN ('Consumption', 'Reservation', 'DevTestConsumption')",
    ),
    Check(
        "prices_complete_scope_not_truncated",
        "prices",
        "warn",
        "full-catalogue scopes were fetched to the last page (a sampled run trips this)",
        "SELECT DISTINCT snapshot_id, scope FROM prices "
        "WHERE NOT scope_complete AND scope <> 'region_spaincentral_page1'",
    ),
    Check(
        "prices_effective_date_sane",
        "prices",
        "warn",
        "effective start date is set and not more than a year after the snapshot",
        "SELECT snapshot_id, price_key, effective_start_date FROM prices "
        "WHERE effective_start_date IS NULL OR effective_start_date > snapshot_ts + INTERVAL 366 DAY",
    ),
    # --- Microsoft 365 roadmap ------------------------------------------------------------
    Check(
        "roadmap_feature_unique",
        "roadmap_features",
        "error",
        "feature_id is unique within a snapshot",
        "SELECT snapshot_id, feature_id, count(*) AS n FROM roadmap_features GROUP BY ALL HAVING n > 1",
    ),
    Check(
        "roadmap_live_feed_complete",
        "roadmap_features",
        "error",
        "a real live roadmap snapshot has at least 500 features (guards against a truncated feed)",
        "SELECT snapshot_id, count(*) AS n FROM roadmap_features "
        "WHERE origin = 'live' AND NOT is_synthetic GROUP BY ALL HAVING n < 500",
    ),
    Check(
        "roadmap_title_present",
        "roadmap_features",
        "error",
        "every feature has a title and a status",
        "SELECT snapshot_id, feature_id FROM roadmap_features "
        "WHERE title IS NULL OR title = '' OR status IS NULL",
    ),
    Check(
        "roadmap_status_known",
        "roadmap_features",
        "warn",
        "status is one of the published roadmap statuses",
        "SELECT DISTINCT status FROM roadmap_features "
        "WHERE status NOT IN ('In development', 'Rolling out', 'Launched', 'Cancelled')",
    ),
    Check(
        "roadmap_ga_label_parsed",
        "roadmap_features",
        "warn",
        "every non-empty GA label parses to a month ('November CY2026')",
        "SELECT DISTINCT ga_label FROM roadmap_features WHERE ga_label IS NOT NULL AND ga_month IS NULL",
    ),
    Check(
        "roadmap_tags_reference_features",
        "roadmap_feature_tags",
        "error",
        "every tag belongs to a feature in the same snapshot",
        "SELECT t.snapshot_id, t.feature_id FROM roadmap_feature_tags t "
        "ANTI JOIN roadmap_features f USING (snapshot_id, feature_id)",
    ),
    # --- GitHub ---------------------------------------------------------------------------
    Check(
        "gh_commit_week_unique",
        "gh_commit_weeks",
        "error",
        "one row per repo and week in a snapshot, commits >= 0",
        "SELECT snapshot_id, repo, week_start FROM gh_commit_weeks "
        "GROUP BY ALL HAVING count(*) > 1 OR min(commits) < 0",
    ),
    Check(
        "gh_pull_dates_ordered",
        "gh_pulls",
        "error",
        "a PR is merged/closed no earlier than it was created",
        "SELECT snapshot_id, repo, pr_number FROM gh_pulls "
        "WHERE (merged_at IS NOT NULL AND merged_at < created_at) "
        "OR (closed_at IS NOT NULL AND closed_at < created_at)",
    ),
    Check(
        "gh_pull_unique",
        "gh_pulls",
        "error",
        "one row per repo and PR number in a snapshot",
        "SELECT snapshot_id, repo, pr_number FROM gh_pulls GROUP BY ALL HAVING count(*) > 1",
    ),
    Check(
        "gh_weekly_commits_present",
        "gh_repos",
        "warn",
        "GitHub returned 52 weeks of participation (commit) statistics for every repo",
        "SELECT r.snapshot_id, r.repo FROM gh_repos r "
        "ANTI JOIN gh_commit_weeks w USING (snapshot_id, repo)",
    ),
]


MANIFEST_COLUMNS = ("source", "dataset", "scope", "snapshot_id", "origin", "item_count")


def connect(silver_dir: Path, manifests: list[dict] | None = None) -> duckdb.DuckDBPyConnection:
    """DuckDB views over every silver table, plus ``bronze_manifests`` (what bronze landed).

    The manifest table lets a check see snapshots that produced *no* silver rows at all, which
    a query over silver alone cannot.
    """
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(
        "CREATE TABLE bronze_manifests (source VARCHAR, dataset VARCHAR, scope VARCHAR, "
        "snapshot_id VARCHAR, origin VARCHAR, item_count BIGINT)"
    )
    rows = [tuple(m.get(c) for c in MANIFEST_COLUMNS) for m in manifests or []]
    if rows:
        con.executemany("INSERT INTO bronze_manifests VALUES (?, ?, ?, ?, ?, ?)", rows)
    for table in SCHEMAS:
        glob = (silver_dir / table).as_posix() + "/**/*.parquet"
        con.execute(
            f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{glob}', union_by_name = true)"
        )
    return con


def run(
    silver_dir: Path,
    checks: list[Check] | None = None,
    *,
    manifests: list[dict] | None = None,
) -> list[CheckResult]:
    con = connect(silver_dir, manifests)
    results = []
    for c in checks or CHECKS:
        rel = con.sql(c.sql)
        cols = rel.columns
        rows = rel.fetchall()
        sample = [
            {
                k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in zip(cols, r, strict=True)
            }
            for r in rows[:5]
        ]
        results.append(
            CheckResult(c.name, c.table, c.severity, c.description, len(rows), not rows, sample)
        )
        level = (
            logging.INFO
            if not rows
            else (logging.ERROR if c.severity == "error" else logging.WARNING)
        )
        log.log(
            level,
            "dq %-40s %-5s %s (%d)",
            c.name,
            c.severity,
            "PASS" if not rows else "FAIL",
            len(rows),
        )
    return results


def failed_errors(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.passed and r.severity == "error"]


def record(lake: Lake, results: list[CheckResult], run_id: str | None = None) -> str:
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    body = {
        "run_id": run_id,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "passed": not failed_errors(results),
        "results": [asdict(r) for r in results],
    }
    path = f"dq_results/{run_id}.json"
    data = json.dumps(body, indent=1, default=str).encode()
    lake.put("ops", path, data)
    lake.put("ops", "dq_results/latest.json", data)
    return path
