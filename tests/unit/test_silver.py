from __future__ import annotations

from datetime import UTC, date, datetime

import pyarrow.parquet as pq
import pytest

from azm365 import bronze, silver
from azm365.lake import FileLake


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("November CY2026", date(2026, 11, 1)),
        ("january 2025", date(2025, 1, 1)),
        ("Q3 CY2025", date(2025, 7, 1)),
        ("", None),
        (None, None),
        ("TBD", None),
        ("Smarch CY2026", None),
    ],
)
def test_parse_roadmap_month(label, expected):
    assert silver.parse_roadmap_month(label) == expected


def test_parse_ts_handles_z_offsets_and_naive():
    assert silver.parse_ts("2026-01-05T10:00:00Z") == datetime(2026, 1, 5, 10, tzinfo=UTC)
    assert silver.parse_ts("2026-01-05T10:00:00") == datetime(2026, 1, 5, 10, tzinfo=UTC)
    assert silver.parse_ts("2026-01-05T15:30:00+05:30") == datetime(2026, 1, 5, 10, tzinfo=UTC)
    assert silver.parse_ts(None) is None


REC = {
    "source": "azure_prices",
    "dataset": "retail_prices",
    "scope": "s",
    "snapshot_id": "20260105T000000Z-live",
    "snapshot_ts": "2026-01-05T00:00:00+00:00",
    "origin": "live",
    "complete": True,
    "synthetic": True,
}


def _item(price, effective, region="eastus"):
    return {
        "meterId": "m1",
        "skuId": "s1",
        "type": "Consumption",
        "tierMinimumUnits": 0.0,
        "armRegionName": region,
        "unitOfMeasure": "1 Hour",
        "currencyCode": "USD",
        "retailPrice": price,
        "unitPrice": price,
        "effectiveStartDate": effective,
        "serviceName": "Virtual Machines",
    }


def test_price_rows_dedupe_keeps_latest_effective_price():
    items = [
        _item(1.0, "2025-01-01T00:00:00Z"),
        _item(0.9, "2025-06-01T00:00:00Z"),
        _item(2.0, "2025-01-01T00:00:00Z", "westus"),
    ]
    rows = silver.rows_prices(REC, {"items": items})["prices"]
    assert len(rows) == 2
    east = next(r for r in rows if r["arm_region_name"] == "eastus")
    assert east["retail_price"] == 0.9
    assert east["is_synthetic"]
    assert east["scope_complete"]


def test_price_key_distinguishes_region_and_reservation_term():
    a = _item(1.0, None)
    assert silver.price_key(a) != silver.price_key({**a, "armRegionName": "westus"})
    assert silver.price_key(a) != silver.price_key({**a, "reservationTerm": "1 Year"})
    assert silver.price_key(a) == silver.price_key(dict(a))


def test_roadmap_rows_tags_and_copilot_flag():
    rec = {**REC, "source": "m365_roadmap", "dataset": "features", "scope": "all"}
    items = [
        {
            "id": 1,
            "title": "Word: something",
            "status": "In development",
            "publicDisclosureAvailabilityDate": "March CY2026",
            "tagsContainer": {
                "products": [{"tagName": "Microsoft Copilot (Microsoft 365)"}, {"tagName": "Word"}],
                "platforms": [{"tagName": "Web"}],
            },
        },
        {"id": 2, "title": "Teams thing", "status": "Launched", "tags": [{"tagName": "Teams"}]},
        {"id": 1, "title": "duplicate", "status": "Launched"},
    ]
    out = silver.rows_roadmap(rec, {"items": items})
    feats = {f["feature_id"]: f for f in out["roadmap_features"]}
    assert set(feats) == {1, 2}
    assert feats[1]["mentions_copilot"]
    assert not feats[2]["mentions_copilot"]
    assert feats[1]["ga_month"] == date(2026, 3, 1)
    tags = {(t["feature_id"], t["tag_type"], t["tag_name"]) for t in out["roadmap_feature_tags"]}
    assert (1, "product", "Word") in tags
    assert (1, "platform", "Web") in tags
    assert (2, "untyped", "Teams") in tags


def test_github_weeks_are_anchored_on_sundays_ending_this_week():
    rec = {
        **REC,
        "source": "github",
        "dataset": "repo_activity",
        "scope": "o__r",
        "snapshot_ts": "2026-01-07T00:00:00+00:00",
    }
    payload = {
        "repo": {"full_name": "o/r"},
        "weekly_commits": [1, 2, 3],
        "releases": [],
        "pulls": [],
    }
    weeks = silver.rows_github(rec, payload)["gh_commit_weeks"]
    assert [w["week_start"] for w in weeks] == [
        date(2025, 12, 21),
        date(2025, 12, 28),
        date(2026, 1, 4),
    ]
    assert all(w["week_start"].weekday() == 6 for w in weeks)


def test_silver_build_is_incremental(tmp_path):
    lake = FileLake(tmp_path)
    ref = bronze.SnapshotRef("azure_prices", "retail_prices", "s", "20260105T000000Z-live")
    common = {"item_count": 1, "complete": True, "request": {}}
    bronze.write_snapshot(lake, ref, {"items": [_item(1.0, None)]}, **common)
    assert silver.build(lake)["built"] == 1
    assert silver.build(lake) == {"built": 0, "skipped": 1}
    # A re-landed snapshot with different bytes is rebuilt in place.
    bronze.write_snapshot(lake, ref, {"items": [_item(2.0, None)]}, **common, force=True)
    assert silver.build(lake)["built"] == 1
    tbl = pq.read_table(tmp_path / "silver" / "prices" / "s" / "20260105T000000Z-live.parquet")
    assert tbl.column("retail_price").to_pylist() == [2.0]
    assert tbl.schema.equals(silver.SCHEMAS["prices"])


def test_ensure_empty_tables_creates_readable_placeholders(tmp_path):
    silver.ensure_empty_tables(tmp_path)
    for table in silver.SCHEMAS:
        f = tmp_path / table / "_empty" / "empty.parquet"
        assert pq.read_table(f).num_rows == 0
