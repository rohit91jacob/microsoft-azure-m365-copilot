"""Silver: typed, conformed Parquet tables, one file per (table, scope, bronze snapshot).

Incremental and idempotent: a state object records the bronze sha256 each snapshot was built
from, so unchanged snapshots are skipped and a re-landed (``--force``) snapshot is rebuilt in
place. Every row carries its lineage (snapshot_id, snapshot_ts, origin, scope, completeness and
whether it is synthetic fixture data).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from azm365 import bronze
from azm365.lake import Lake
from azm365.settings import Settings

log = logging.getLogger(__name__)

LINEAGE = [
    ("snapshot_id", pa.string()),
    ("snapshot_ts", pa.timestamp("us", tz="UTC")),
    ("origin", pa.string()),
    ("scope", pa.string()),
    ("scope_complete", pa.bool_()),
    ("is_synthetic", pa.bool_()),
]


def _schema(*cols: tuple[str, pa.DataType]) -> pa.Schema:
    return pa.schema([*LINEAGE, *cols])


SCHEMAS: dict[str, pa.Schema] = {
    "prices": _schema(
        ("price_key", pa.string()),
        ("currency_code", pa.string()),
        ("meter_id", pa.string()),
        ("meter_name", pa.string()),
        ("sku_id", pa.string()),
        ("sku_name", pa.string()),
        ("arm_sku_name", pa.string()),
        ("product_id", pa.string()),
        ("product_name", pa.string()),
        ("service_id", pa.string()),
        ("service_name", pa.string()),
        ("service_family", pa.string()),
        ("arm_region_name", pa.string()),
        ("location", pa.string()),
        ("price_type", pa.string()),
        ("reservation_term", pa.string()),
        ("unit_of_measure", pa.string()),
        ("tier_minimum_units", pa.float64()),
        ("retail_price", pa.float64()),
        ("unit_price", pa.float64()),
        ("effective_start_date", pa.timestamp("us", tz="UTC")),
        ("is_primary_meter_region", pa.bool_()),
    ),
    "roadmap_features": _schema(
        ("feature_id", pa.int64()),
        ("title", pa.string()),
        ("status", pa.string()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("modified_at", pa.timestamp("us", tz="UTC")),
        ("ga_label", pa.string()),
        ("ga_month", pa.date32()),
        ("preview_label", pa.string()),
        ("preview_month", pa.date32()),
        ("more_info_link", pa.string()),
        ("mentions_copilot", pa.bool_()),
    ),
    "roadmap_feature_tags": _schema(
        ("feature_id", pa.int64()),
        ("tag_type", pa.string()),
        ("tag_name", pa.string()),
    ),
    "gh_repos": _schema(
        ("repo", pa.string()),
        ("stars", pa.int64()),
        ("forks", pa.int64()),
        ("open_issues", pa.int64()),
        ("watchers", pa.int64()),
        ("language", pa.string()),
        ("license", pa.string()),
        ("default_branch", pa.string()),
        ("archived", pa.bool_()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("pushed_at", pa.timestamp("us", tz="UTC")),
    ),
    "gh_commit_weeks": _schema(
        ("repo", pa.string()),
        ("week_start", pa.date32()),
        ("commits", pa.int64()),
    ),
    "gh_releases": _schema(
        ("repo", pa.string()),
        ("release_id", pa.int64()),
        ("tag_name", pa.string()),
        ("release_name", pa.string()),
        ("draft", pa.bool_()),
        ("prerelease", pa.bool_()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("published_at", pa.timestamp("us", tz="UTC")),
        ("assets", pa.int64()),
    ),
    "gh_pulls": _schema(
        ("repo", pa.string()),
        ("pr_number", pa.int64()),
        ("state", pa.string()),
        ("draft", pa.bool_()),
        ("created_at", pa.timestamp("us", tz="UTC")),
        ("closed_at", pa.timestamp("us", tz="UTC")),
        ("merged_at", pa.timestamp("us", tz="UTC")),
        ("updated_at", pa.timestamp("us", tz="UTC")),
        ("base_ref", pa.string()),
        ("author_is_bot", pa.bool_()),
        ("author_association", pa.string()),
    ),
}

SOURCE_TABLES = {
    "azure_prices": ("prices",),
    "m365_roadmap": ("roadmap_features", "roadmap_feature_tags"),
    "github": ("gh_repos", "gh_commit_weeks", "gh_releases", "gh_pulls"),
}

MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}
_MONTH_RE = re.compile(r"^\s*([A-Za-z]+)\s+(?:CY)?\s*(\d{4})\s*$")
_QUARTER_RE = re.compile(r"^\s*Q([1-4])\s+(?:CY)?\s*(\d{4})\s*$", re.IGNORECASE)


def parse_roadmap_month(label: str | None) -> date | None:
    """'November CY2026' -> 2026-11-01; 'Q3 CY2025' -> 2025-07-01; anything else -> None."""
    if not label:
        return None
    m = _MONTH_RE.match(label)
    if m and m.group(1).lower() in MONTHS:
        return date(int(m.group(2)), MONTHS[m.group(1).lower()], 1)
    q = _QUARTER_RE.match(label)
    if q:
        return date(int(q.group(2)), 3 * (int(q.group(1)) - 1) + 1, 1)
    return None


def parse_ts(value: Any) -> datetime | None:
    """ISO-8601 with or without offset/'Z'; naive values are taken as UTC."""
    if value in (None, ""):
        return None
    s = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def price_key(item: dict[str, Any]) -> str:
    parts = [
        item.get("meterId"),
        item.get("skuId"),
        item.get("type"),
        item.get("reservationTerm"),
        item.get("tierMinimumUnits"),
        item.get("armRegionName"),
        item.get("unitOfMeasure"),
        item.get("currencyCode"),
    ]
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


def _lineage(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": rec["snapshot_id"],
        "snapshot_ts": parse_ts(rec["snapshot_ts"]),
        "origin": rec["origin"],
        "scope": rec["scope"],
        "scope_complete": bool(rec["complete"]),
        "is_synthetic": bool(rec.get("synthetic", False)),
    }


def rows_prices(rec: dict[str, Any], payload: dict[str, Any]) -> dict[str, list[dict]]:
    base = _lineage(rec)
    by_key: dict[str, dict[str, Any]] = {}
    for it in payload["items"]:
        row = {
            **base,
            "price_key": price_key(it),
            "currency_code": it.get("currencyCode"),
            "meter_id": it.get("meterId"),
            "meter_name": it.get("meterName"),
            "sku_id": it.get("skuId"),
            "sku_name": it.get("skuName"),
            "arm_sku_name": it.get("armSkuName") or None,
            "product_id": it.get("productId"),
            "product_name": it.get("productName"),
            "service_id": it.get("serviceId"),
            "service_name": it.get("serviceName"),
            "service_family": it.get("serviceFamily"),
            "arm_region_name": it.get("armRegionName") or None,
            "location": it.get("location") or None,
            "price_type": it.get("type"),
            "reservation_term": it.get("reservationTerm"),
            "unit_of_measure": it.get("unitOfMeasure"),
            "tier_minimum_units": it.get("tierMinimumUnits"),
            "retail_price": it.get("retailPrice"),
            "unit_price": it.get("unitPrice"),
            "effective_start_date": parse_ts(it.get("effectiveStartDate")),
            "is_primary_meter_region": it.get("isPrimaryMeterRegion"),
        }
        prev = by_key.get(row["price_key"])
        # Overlapping filters can return the same price point twice; when the API lists an old
        # and a new effective price for one key, the latest effective date wins.
        if prev is None or (row["effective_start_date"] or datetime.min.replace(tzinfo=UTC)) > (
            prev["effective_start_date"] or datetime.min.replace(tzinfo=UTC)
        ):
            by_key[row["price_key"]] = row
    return {"prices": list(by_key.values())}


TAG_TYPES = {
    "products": "product",
    "cloudInstances": "cloud_instance",
    "releasePhase": "release_phase",
    "platforms": "platform",
}


def rows_roadmap(rec: dict[str, Any], payload: dict[str, Any]) -> dict[str, list[dict]]:
    base = _lineage(rec)
    features, tags = [], []
    seen: set[int] = set()
    for it in payload["items"]:
        fid = int(it["id"])
        if fid in seen:
            continue
        seen.add(fid)
        container = it.get("tagsContainer") or {}
        products = [t["tagName"] for t in container.get("products") or []]
        title = (it.get("title") or "").strip()
        features.append(
            {
                **base,
                "feature_id": fid,
                "title": title,
                "status": it.get("status") or None,
                "created_at": parse_ts(it.get("created")),
                "modified_at": parse_ts(it.get("modified")),
                "ga_label": it.get("publicDisclosureAvailabilityDate") or None,
                "ga_month": parse_roadmap_month(it.get("publicDisclosureAvailabilityDate")),
                "preview_label": it.get("publicPreviewDate") or None,
                "preview_month": parse_roadmap_month(it.get("publicPreviewDate")),
                "more_info_link": it.get("moreInfoLink") or None,
                "mentions_copilot": "copilot" in title.lower()
                or any("copilot" in p.lower() for p in products),
            }
        )
        if container:
            for key, tag_type in TAG_TYPES.items():
                for name in {t["tagName"].strip() for t in container.get(key) or []}:
                    tags.append({**base, "feature_id": fid, "tag_type": tag_type, "tag_name": name})
        else:
            for name in {t["tagName"].strip() for t in it.get("tags") or []}:
                tags.append({**base, "feature_id": fid, "tag_type": "untyped", "tag_name": name})
    return {"roadmap_features": features, "roadmap_feature_tags": tags}


def rows_github(rec: dict[str, Any], payload: dict[str, Any]) -> dict[str, list[dict]]:
    base = _lineage(rec)
    repo_meta = payload["repo"]
    repo = repo_meta["full_name"]
    repos = [
        {
            **base,
            "repo": repo,
            "stars": repo_meta.get("stargazers_count"),
            "forks": repo_meta.get("forks_count"),
            "open_issues": repo_meta.get("open_issues_count"),
            "watchers": repo_meta.get("subscribers_count"),
            "language": repo_meta.get("language"),
            "license": repo_meta.get("license"),
            "default_branch": repo_meta.get("default_branch"),
            "archived": bool(repo_meta.get("archived")),
            "created_at": parse_ts(repo_meta.get("created_at")),
            "pushed_at": parse_ts(repo_meta.get("pushed_at")),
        }
    ]
    # Participation counts are oldest-first and end with the current (partial) week. Weeks are
    # anchored on Sundays 00:00 UTC, GitHub's statistics week, counted back from the snapshot.
    counts = payload.get("weekly_commits") or []
    snap = base["snapshot_ts"]
    this_week = (snap - timedelta(days=(snap.weekday() + 1) % 7)).date()
    weeks = [
        {
            **base,
            "repo": repo,
            "week_start": this_week - timedelta(weeks=len(counts) - 1 - i),
            "commits": int(n),
        }
        for i, n in enumerate(counts)
    ]
    releases = [
        {
            **base,
            "repo": repo,
            "release_id": r["id"],
            "tag_name": r["tag_name"],
            "release_name": r.get("name"),
            "draft": r["draft"],
            "prerelease": r["prerelease"],
            "created_at": parse_ts(r.get("created_at")),
            "published_at": parse_ts(r.get("published_at")),
            "assets": r.get("assets", 0),
        }
        for r in payload.get("releases") or []
    ]
    pulls_by_number = {
        p["number"]: {
            **base,
            "repo": repo,
            "pr_number": p["number"],
            "state": p["state"],
            "draft": p["draft"],
            "created_at": parse_ts(p["created_at"]),
            "closed_at": parse_ts(p.get("closed_at")),
            "merged_at": parse_ts(p.get("merged_at")),
            "updated_at": parse_ts(p.get("updated_at")),
            "base_ref": p.get("base_ref"),
            "author_is_bot": p.get("author_is_bot", False),
            "author_association": p.get("author_association"),
        }
        # Page boundaries shift while PRs are being updated; keep one row per PR.
        for p in payload.get("pulls") or []
    }
    return {
        "gh_repos": repos,
        "gh_commit_weeks": weeks,
        "gh_releases": releases,
        "gh_pulls": list(pulls_by_number.values()),
    }


PARSERS: dict[str, Callable[[dict, dict], dict[str, list[dict]]]] = {
    "azure_prices": rows_prices,
    "m365_roadmap": rows_roadmap,
    "github": rows_github,
}


def to_parquet(table: str, rows: Iterable[dict[str, Any]]) -> bytes:
    schema = SCHEMAS[table]
    tbl = pa.Table.from_pylist(list(rows), schema=schema)
    buf = io.BytesIO()
    pq.write_table(tbl, buf, compression="zstd")
    return buf.getvalue()


def silver_path(table: str, rec: dict[str, Any]) -> str:
    return f"{table}/{rec['scope']}/{rec['snapshot_id']}.parquet"


def state_path(rec: dict[str, Any]) -> str:
    return f"_state/{rec['source']}/{rec['dataset']}/{rec['scope']}/{rec['snapshot_id']}.json"


def build(lake: Lake, *, full_refresh: bool = False, mirror: Path | None = None) -> dict[str, int]:
    """Bronze -> silver for every manifest entry not yet built from the same bronze bytes.

    ``mirror`` (Blob/ADLS lakes only) receives a write-through copy of every rebuilt file.
    """
    built = skipped = 0
    rows_written: dict[str, int] = {}
    for rec in bronze.list_manifests(lake):
        parser = PARSERS.get(rec["source"])
        if parser is None:
            continue
        sp = state_path(rec)
        if not full_refresh and lake.exists("silver", sp):
            state = json.loads(lake.get("silver", sp))
            if state.get("bronze_sha256") == rec["sha256"]:
                skipped += 1
                continue
        envelope = bronze.read_snapshot(lake, rec)
        tables = parser(rec, envelope["payload"])
        for table in SOURCE_TABLES[rec["source"]]:
            rows = tables.get(table, [])
            data = to_parquet(table, rows)
            lake.put("silver", silver_path(table, rec), data)
            if mirror is not None:
                target = mirror / silver_path(table, rec)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            rows_written[table] = rows_written.get(table, 0) + len(rows)
        state = {
            "bronze_sha256": rec["sha256"],
            "tables": {t: len(tables.get(t, [])) for t in SOURCE_TABLES[rec["source"]]},
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        lake.put("silver", sp, json.dumps(state).encode())
        built += 1
    log.info(
        "silver: built %d snapshot(s), skipped %d unchanged; rows %s", built, skipped, rows_written
    )
    return {"built": built, "skipped": skipped, **{f"rows_{k}": v for k, v in rows_written.items()}}


def read_dir(settings: Settings, lake: Lake) -> Path:
    """Directory that holds every silver Parquet file, for DuckDB/dbt to scan.

    The local lake is read in place. A Blob/ADLS lake is mirrored to ``settings.silver_mirror``
    (only missing or changed objects are downloaded), the stand-in for Synapse serverless
    reading ADLS directly. Files rebuilt by :func:`build` are written through to the mirror, so
    only objects the mirror has never seen (a fresh Function instance) are downloaded here.
    """
    if lake.kind == "local":
        return settings.lake_root / "silver"
    mirror = settings.silver_mirror
    for name in lake.list("silver"):
        target = mirror / name
        if name.endswith(".parquet") and not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(lake.get("silver", name))
    return mirror


def mirror_for(settings: Settings, lake: Lake) -> Path | None:
    return None if lake.kind == "local" else settings.silver_mirror


def ensure_empty_tables(silver_dir: Path) -> None:
    """Give every silver table at least one (empty) file so dbt sources always resolve."""
    for table in SCHEMAS:
        d = silver_dir / table
        if not d.exists() or not any(d.rglob("*.parquet")):
            (d / "_empty").mkdir(parents=True, exist_ok=True)
            (d / "_empty" / "empty.parquet").write_bytes(to_parquet(table, []))
