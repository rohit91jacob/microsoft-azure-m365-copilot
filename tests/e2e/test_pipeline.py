"""End-to-end: extract -> bronze -> silver -> DQ -> dbt build -> gold -> site, on SIMULATED fixtures."""

from __future__ import annotations

import json

import duckdb
import pyarrow.parquet as pq
import pytest

from azm365 import bronze, report
from azm365.lake import FileLake

pytestmark = pytest.mark.e2e


def q(settings, sql):
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        return con.sql(sql).fetchall()
    finally:
        con.close()


def test_both_days_land_and_build(pipeline_runs):
    _settings, (day1, day2) = pipeline_runs
    # Day 1: 5 price scopes + 1 roadmap + 4 repos live, plus 1 price and 2 roadmap Wayback captures.
    assert day1["landed"] == 13
    assert day2["landed"] == 10  # Wayback captures were already landed on day 1
    assert day2["silver"]["skipped"] == 13
    assert day1["dq"]["checks"] == day2["dq"]["checks"] == 18
    assert set(day2["gold"]) >= {
        "mart_price_changes",
        "mart_cheapest_region",
        "mart_region_price_index",
        "mart_roadmap_cadence",
        "mart_roadmap_slippage",
        "mart_repo_weekly_throughput",
    }


def test_every_bronze_object_matches_its_manifest(pipeline_runs):
    settings, _ = pipeline_runs
    lake = FileLake(settings.lake_root)
    manifests = bronze.list_manifests(lake)
    assert len(manifests) == 23
    for m in manifests:
        assert m["synthetic"] is True
        bronze.read_snapshot(lake, m)  # raises on sha256 mismatch


def test_price_marts(pipeline_runs):
    settings, _ = pipeline_runs
    pairs = q(
        settings,
        "SELECT scope, from_origin, to_origin, price_points_compared, price_points_changed "
        "FROM marts.mart_price_change_summary ORDER BY scope, to_snapshot_ts",
    )
    assert ("region_spaincentral_page1", "wayback", "live") in {p[:3] for p in pairs}
    assert all(changed <= compared for *_, compared, changed in pairs)
    assert q(settings, "SELECT count(*) FROM marts.mart_price_changes")[0][0] > 0
    # Excluded sovereign region never wins the cheapest-region ranking.
    regions = {r[0] for r in q(settings, "SELECT cheapest_region FROM marts.mart_cheapest_region")}
    assert regions and "usgovvirginia" not in regions
    [(idx,)] = q(
        settings,
        "SELECT price_index FROM marts.mart_region_price_index "
        "WHERE service_name = 'Virtual Machines' AND arm_region_name = 'eastus'",
    )
    assert idx == pytest.approx(1.0)
    assert all(r[0] for r in q(settings, "SELECT is_synthetic FROM marts.mart_price_changes"))


def test_roadmap_marts(pipeline_runs):
    settings, _ = pipeline_runs
    [(features, copilot)] = q(
        settings,
        "SELECT count(DISTINCT feature_id), count(DISTINCT feature_id) FILTER (WHERE mentions_copilot) "
        "FROM intermediate.int_roadmap_latest",
    )
    assert features == 60
    assert 0 < copilot < features
    # The renamed product tag is folded into its canonical name by the alias seed.
    products = {
        r[0] for r in q(settings, "SELECT DISTINCT product FROM marts.mart_roadmap_cadence")
    }
    assert "Microsoft Purview" in products
    assert "Microsoft Purview compliance portal" not in products
    slips = q(settings, "SELECT direction, months_moved FROM marts.mart_roadmap_slippage")
    assert slips
    assert {d for d, _ in slips} <= {"slipped", "pulled_in"}


def test_engineering_marts(pipeline_runs):
    settings, _ = pipeline_runs
    rows = q(
        settings,
        "SELECT repo, commits_52w, merged_prs_in_window FROM marts.mart_repo_summary ORDER BY repo",
    )
    assert [r[0] for r in rows] == [
        "microsoft/TypeScript",
        "microsoft/autogen",
        "microsoft/semantic-kernel",
        "microsoft/vscode",
    ]
    assert all(r[1] > 0 and r[2] > 0 for r in rows)
    [(weeks,)] = q(settings, "SELECT count(*) FROM marts.mart_repo_weekly_throughput")
    assert weeks == 4 * 52
    # The newest simulated release of every repo is a side-stream tag (side-tool/v0.3.0); the
    # mart reports the repo's main stream from the repo_release_streams seed instead.
    tags = dict(q(settings, "SELECT repo, latest_release_tag FROM marts.mart_repo_summary"))
    assert tags == {
        "microsoft/TypeScript": "v1.20.0",
        "microsoft/autogen": "python-v1.20.0",
        "microsoft/semantic-kernel": "python-1.20.0",
        "microsoft/vscode": "1.20.0",
    }


def test_gold_is_published_as_parquet(pipeline_runs):
    settings, (_, day2) = pipeline_runs
    for mart, n in day2["gold"].items():
        tbl = pq.read_table(settings.lake_root / "gold" / mart / f"{mart}.parquet")
        assert tbl.num_rows == n


def test_report_site_is_labelled_simulated(pipeline_runs, tmp_path):
    settings, _ = pipeline_runs
    summary = report.build_site(settings, tmp_path / "site")
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert summary["synthetic"] is True
    assert "SIMULATED DATA" in page
    assert "usage</b> telemetry is not public" in page
    data = json.loads((tmp_path / "site" / "summary.json").read_text(encoding="utf-8"))
    assert data["roadmap"]["features"] == 60
    assert data["dq"]["passed"] is True
