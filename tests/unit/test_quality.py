from __future__ import annotations

from datetime import UTC, datetime

import pytest

from azm365 import bronze, cli, pipeline, quality, silver
from azm365.lake import FileLake

TS = datetime(2026, 1, 5, tzinfo=UTC)
LINEAGE = {
    "snapshot_id": "20260105T000000Z-live",
    "snapshot_ts": TS,
    "origin": "live",
    "scope": "s",
    "scope_complete": True,
    "is_synthetic": False,
}


def price(**kw):
    row = {
        **LINEAGE,
        "price_key": "k1",
        "currency_code": "USD",
        "meter_id": "m",
        "sku_id": "s",
        "service_name": "Storage",
        "price_type": "Consumption",
        "unit_of_measure": "1 GB/Month",
        "retail_price": 1.0,
        "unit_price": 1.0,
        "effective_start_date": TS,
    }
    return {**row, **kw}


def write(silver_dir, table, rows):
    path = silver_dir / table / "s" / "x.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(silver.to_parquet(table, rows))


def results_by_name(silver_dir):
    silver.ensure_empty_tables(silver_dir)
    return {r.name: r for r in quality.run(silver_dir)}


def test_clean_silver_passes_every_check(tmp_path):
    write(tmp_path, "prices", [price(), price(price_key="k2")])
    res = results_by_name(tmp_path)
    assert not quality.failed_errors(list(res.values()))
    assert all(r.passed for r in res.values())


def test_duplicate_and_negative_prices_fail_error_checks(tmp_path):
    write(
        tmp_path,
        "prices",
        [price(), price(), price(price_key="k3", retail_price=-1.0, currency_code="EUR")],
    )
    res = results_by_name(tmp_path)
    failed = {r.name for r in quality.failed_errors(list(res.values()))}
    assert {"prices_key_unique", "prices_non_negative", "prices_currency_usd"} <= failed
    assert res["prices_key_unique"].sample[0]["n"] == 2


def test_truncated_real_roadmap_feed_fails(tmp_path):
    feature = {**LINEAGE, "feature_id": 1, "title": "t", "status": "Launched"}
    write(tmp_path, "roadmap_features", [feature])
    res = results_by_name(tmp_path)
    assert not res["roadmap_live_feed_complete"].passed  # 1 < 500 features in a real live pull
    write(tmp_path, "roadmap_features", [{**feature, "is_synthetic": True}])
    assert results_by_name(tmp_path)["roadmap_live_feed_complete"].passed


def test_orphan_tags_and_bad_pr_dates_fail(tmp_path):
    write(
        tmp_path,
        "roadmap_feature_tags",
        [{**LINEAGE, "feature_id": 9, "tag_type": "product", "tag_name": "x"}],
    )
    write(
        tmp_path,
        "gh_pulls",
        [
            {
                **LINEAGE,
                "repo": "o/r",
                "pr_number": 1,
                "created_at": TS,
                "merged_at": datetime(2025, 1, 1, tzinfo=UTC),
            }
        ],
    )
    failed = {r.name for r in quality.failed_errors(list(results_by_name(tmp_path).values()))}
    assert {"roadmap_tags_reference_features", "gh_pull_dates_ordered"} <= failed


def test_warn_checks_do_not_block(tmp_path):
    write(tmp_path, "prices", [price(price_type="Spot")])
    res = list(results_by_name(tmp_path).values())
    assert not quality.failed_errors(res)
    assert any(not r.passed and r.severity == "warn" for r in res)


def test_pipeline_dq_gate_raises_and_cli_exits_3(settings, monkeypatch):
    lake = FileLake(settings.lake_root)
    ref = bronze.SnapshotRef("azure_prices", "retail_prices", "s", "20260105T000000Z-live")
    item = {
        "meterId": "m",
        "skuId": "s",
        "type": "Consumption",
        "currencyCode": "USD",
        "retailPrice": -5,
        "unitPrice": -5,
        "serviceName": "X",
        "unitOfMeasure": "1",
    }
    bronze.write_snapshot(lake, ref, {"items": [item]}, item_count=1, complete=True, request={})
    pipeline.build_silver(settings, lake=lake)
    with pytest.raises(pipeline.DataQualityError, match="prices_non_negative"):
        pipeline.run_dq(settings, lake=lake)
    assert lake.exists("ops", "dq_results/latest.json")
    monkeypatch.setenv("AZM365_DATA_DIR", str(settings.data_dir))
    assert cli.main(["dq"]) == pipeline.EXIT_DQ


def test_empty_live_price_snapshot_fails_error_check(tmp_path):
    # A snapshot that landed in bronze but produced no silver rows is invisible to a query over
    # silver alone; the gate sees it through the bronze manifests.
    write(tmp_path, "prices", [price()])
    silver.ensure_empty_tables(tmp_path)
    manifests = [
        {
            "source": "azure_prices",
            "dataset": "retail_prices",
            "scope": scope,
            "snapshot_id": "20260105T000000Z-live",
            "origin": "live",
            "item_count": n,
        }
        for scope, n in (("s", 1), ("vm_popular_skus", 0))
    ]
    res = {r.name: r for r in quality.run(tmp_path, manifests=manifests)}
    assert not res["prices_not_empty"].passed
    assert res["prices_not_empty"].sample == [
        {"snapshot_id": "20260105T000000Z-live", "scope": "vm_popular_skus", "item_count": 0}
    ]
    ok = {r.name: r for r in quality.run(tmp_path, manifests=manifests[:1])}
    assert ok["prices_not_empty"].passed
