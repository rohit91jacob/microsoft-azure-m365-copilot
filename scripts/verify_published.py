"""Reconcile pipeline outputs with figures Microsoft and the package registries publish elsewhere.

Independent channels, downloaded at runtime (never committed):

* Azure pricing-calculator data behind azure.microsoft.com/pricing (a different Microsoft API
  from the Retail Prices API the pipeline reads): VM pay-as-you-go hourly prices, Functions
  Consumption tiers, Hot LRS block blob capacity tiers.
* The Microsoft 365 roadmap RSS feed (a second official channel for the same roadmap).
* npm and PyPI (release versions of TypeScript and Semantic Kernel) and the VS Code release
  notes page, against the GitHub releases the pipeline extracted.

Usage::

    AZM365_DATA_DIR=... uv run python scripts/verify_published.py [--cache DIR] [--json OUT]

Prints a Markdown summary; every mismatch is listed, nothing is hidden.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from azm365.settings import Settings  # noqa: E402

UA = Settings(data_dir=REPO).user_agent
CALC = {
    "vm": "https://azure.microsoft.com/api/v3/pricing/virtual-machines/calculator/?culture=en-us",
    "functions": "https://azure.microsoft.com/api/v2/pricing/functions/calculator/?culture=en-us",
    "storage": "https://azure.microsoft.com/api/v3/pricing/storage/calculator/?culture=en-us",
}
RSS = "https://www.microsoft.com/releasecommunications/api/v2/m365/rss"
VM_KEYS = {
    "Standard_D2s_v5": "d2sv5",
    "Standard_D4s_v5": "d4sv5",
    "Standard_E4s_v5": "e4sv5",
    "Standard_F4s_v2": "f4sv2",
    "Standard_B2s": "b2s",
    "Standard_NC24ads_A100_v4": "nc24adsv4",
}


def fetch(url: str, cache: Path, name: str) -> bytes:
    path = cache / name
    if path.is_file():
        return path.read_bytes()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=300)
    r.raise_for_status()
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content


def slug_candidates(slug: str) -> set[str]:
    """'us-east-2' -> {'useast2', 'eastus2', ...}: calculator slugs vs ARM region names."""
    parts = slug.split("-")
    digits = [p for p in parts if p.isdigit()]
    words = [p for p in parts if not p.isdigit()]
    out = {
        "".join(parts),
        "".join(reversed(words)) + "".join(digits),
        "".join(words) + "".join(digits),
    }
    aliases = {
        "europe-west": "westeurope",
        "europe-north": "northeurope",
        "asia-pacific-east": "eastasia",
        "asia-pacific-southeast": "southeastasia",
        "us-west-central": "westcentralus",
        "us-north-central": "northcentralus",
        "us-south-central": "southcentralus",
        "japan-east": "japaneast",
        "japan-west": "japanwest",
        "korea-central": "koreacentral",
        "korea-south": "koreasouth",
        "uk-south": "uksouth",
        "uk-west": "ukwest",
    }
    if slug in aliases:
        out.add(aliases[slug])
    return out


def region_map(calc_regions: set[str], arm_regions: set[str]) -> dict[str, str]:
    m = {}
    for slug in calc_regions:
        hits = slug_candidates(slug) & arm_regions
        if len(hits) == 1:
            m[slug] = hits.pop()
    return m


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, 0.0005 * max(abs(a), abs(b)))


def verify_vm(con, calc: dict[str, Any]) -> dict[str, Any]:
    ours = con.sql(
        """
        SELECT arm_sku_name, arm_region_name, product_name, retail_price
        FROM intermediate.int_price_latest
        WHERE scope = 'vm_popular_skus' AND price_type = 'Consumption' AND tier_minimum_units = 0
          AND sku_name NOT LIKE '%Spot%' AND sku_name NOT LIKE '%Low Priority%'
        """
    ).fetchall()
    arm = {r[1] for r in ours}
    offers = calc["offers"]
    slugs = {s for k in offers for s in offers[k].get("prices", {}).get("perhour", {})}
    rmap = region_map(slugs, arm)
    lookup = {}
    for sku, key in VM_KEYS.items():
        for os_name in ("linux", "windows"):
            offer = offers.get(f"{os_name}-{key}-standard")
            if not offer:
                continue
            for slug, v in offer["prices"]["perhour"].items():
                if slug in rmap:
                    lookup[(sku, os_name, rmap[slug])] = v["value"]
    compared, mismatches = 0, []
    for sku, region, product, price in ours:
        os_name = "windows" if "Windows" in product else "linux"
        pub = lookup.get((sku, os_name, region))
        if pub is None:
            continue
        compared += 1
        if not close(pub, price):
            mismatches.append(
                {
                    "sku": sku,
                    "os": os_name,
                    "region": region,
                    "retail_api": price,
                    "calculator": pub,
                }
            )
    d2 = {r[1]: r[3] for r in ours if r[0] == "Standard_D2s_v5" and "Windows" not in r[2]}
    return {
        "compared": compared,
        "matched": compared - len(mismatches),
        "mismatches": mismatches,
        "regions_mapped": len(rmap),
        "d2s_v5_linux_eastus": d2.get("eastus"),
        "calc_d2s_v5_linux_eastus": lookup.get(("Standard_D2s_v5", "linux", "eastus")),
    }


def tiers(entry: dict[str, Any]) -> list[tuple[float, float]]:
    """Calculator graduated prices -> [(tier_minimum_units, price)]."""
    out, lower = [], 0.0
    for p in entry["prices"]:
        out.append((lower, p["price"]["value"]))
        lower = p["limit"]
    return out


def verify_functions(con, calc: dict[str, Any]) -> dict[str, Any]:
    rows = con.sql(
        """
        SELECT arm_region_name, meter_name, tier_minimum_units, retail_price
        FROM intermediate.int_price_latest
        WHERE scope = 'functions' AND product_name = 'Functions' AND price_type = 'Consumption'
          AND meter_name IN ('Standard Execution Time', 'Standard Total Executions')
        """
    ).fetchall()
    arm = {r[0] for r in rows}
    grad = calc["graduatedOffers"]
    rmap = region_map(set(grad["compute-payg"]), arm)
    ours: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for region, meter, tier, price in rows:
        ours.setdefault((region, meter), []).append((tier, price))
    compared, mismatches = 0, []
    for slug, region in rmap.items():
        pub_compute = tiers(grad["compute-payg"][slug])
        ours_compute = sorted(ours.get((region, "Standard Execution Time"), []))
        if ours_compute:
            compared += 1
            if [(t, round(p, 9)) for t, p in ours_compute] != [
                (t, round(p, 9)) for t, p in pub_compute
            ]:
                mismatches.append(
                    {
                        "region": region,
                        "meter": "execution time",
                        "retail_api": ours_compute,
                        "calculator": pub_compute,
                    }
                )
    return {
        "compared": compared,
        "matched": compared - len(mismatches),
        "mismatches": mismatches,
        "eastus_execution_time_tiers": sorted(ours.get(("eastus", "Standard Execution Time"), [])),
        "eastus_total_executions_tiers": sorted(
            ours.get(("eastus", "Standard Total Executions"), [])
        ),
    }


def verify_storage(con, calc: dict[str, Any]) -> dict[str, Any]:
    rows = con.sql(
        """
        SELECT arm_region_name, tier_minimum_units, retail_price
        FROM intermediate.int_price_latest
        WHERE scope = 'blob_storage' AND meter_name = 'Hot LRS Data Stored' AND price_type = 'Consumption'
        """
    ).fetchall()
    arm = {r[0] for r in rows}
    pub = calc["offers"]["general-purpose-v2-block-blob-flat-hot-lrs"]["graduatedPrices"]["pergb"]
    rmap = region_map(set(pub), arm)
    ours: dict[str, list[tuple[float, float]]] = {}
    for region, tier, price in rows:
        ours.setdefault(region, []).append((tier, price))
    compared, mismatches = 0, []
    for slug, region in rmap.items():
        if region not in ours:
            continue
        compared += 1
        a = [(t, round(p, 6)) for t, p in sorted(ours[region])]
        b = [(t, round(p, 6)) for t, p in tiers(pub[slug])]
        if a != b:
            mismatches.append({"region": region, "retail_api": a, "calculator": b})
    return {
        "compared": compared,
        "matched": compared - len(mismatches),
        "mismatches": mismatches,
        "eastus": sorted(ours.get("eastus", [])),
    }


def verify_roadmap(con, rss: bytes) -> dict[str, Any]:
    root = ET.fromstring(rss)  # noqa: S314 - trusted Microsoft feed, no DTDs
    items = root.findall("./channel/item")
    rss_ids, rss_ga = {}, {}
    for it in items:
        guid = (it.findtext("guid") or "").strip()
        if guid.isdigit():
            rss_ids[int(guid)] = [c.text or "" for c in it.findall("category")]
            ga = re.search(r"GA date: ([A-Za-z]+ CY\d{4})", it.findtext("description") or "")
            if ga:
                rss_ga[int(guid)] = ga.group(1)
    latest = con.sql("SELECT max(snapshot_ts) FROM intermediate.int_roadmap_latest").fetchone()[0]
    ours = dict(
        con.sql("SELECT feature_id, status FROM intermediate.int_roadmap_latest").fetchall()
    )
    ours_ga = dict(
        con.sql(
            "SELECT feature_id, ga_label FROM intermediate.int_roadmap_latest WHERE ga_label IS NOT NULL"
        ).fetchall()
    )
    ga_both = set(ours_ga) & set(rss_ga)
    ga_agree = sum(1 for f in ga_both if ours_ga[f] == rss_ga[f])
    both = set(ours) & set(rss_ids)
    status_names = {"In development", "Rolling out", "Launched", "Cancelled"}
    status_agree = sum(1 for f in both if ours[f] in rss_ids[f])
    status_known = sum(1 for f in both if status_names & set(rss_ids[f]))
    return {
        "json_snapshot_ts": str(latest),
        "json_features": len(ours),
        "rss_items": len(items),
        "rss_feature_ids": len(rss_ids),
        "in_both": len(both),
        "only_json": len(set(ours) - set(rss_ids)),
        "only_rss": len(set(rss_ids) - set(ours)),
        "status_compared": status_known,
        "status_agree": status_agree,
        "ga_compared": len(ga_both),
        "ga_agree": ga_agree,
        "ga_differ_examples": [
            {"id": f, "json": ours_ga[f], "rss": rss_ga[f]}
            for f in sorted(ga_both)
            if ours_ga[f] != rss_ga[f]
        ][:10],
        "json_status_counts": dict(Counter(ours.values())),
    }


def verify_github(con, cache: Path) -> list[dict[str, Any]]:
    rel = dict(
        con.sql(
            """
            SELECT repo, latest_release_tag FROM marts.mart_repo_summary
            """
        ).fetchall()
    )
    out = []
    npm = json.loads(
        fetch("https://registry.npmjs.org/typescript/latest", cache, "npm_typescript.json")
    )
    ts = con.sql(
        """
        SELECT tag_name FROM staging.stg_gh_releases
        WHERE repo = 'microsoft/TypeScript' AND regexp_matches(tag_name, '^v[0-9]+[.][0-9]+[.][0-9]+$')
          AND NOT prerelease
        ORDER BY published_at DESC LIMIT 1
        """
    ).fetchone()
    ts_tag = ts[0] if ts else None
    out.append(
        {
            "repo": "microsoft/TypeScript",
            "github_latest_stable": ts_tag,
            "mart_latest_release_tag": rel.get("microsoft/TypeScript"),
            "published": f"npm typescript@latest = {npm['version']}",
            # The mart must report the same (main-stream) tag, not just the raw releases.
            "agree": (ts_tag or "").lstrip("v") == npm["version"]
            and rel.get("microsoft/TypeScript") == ts_tag,
        }
    )
    pypi = json.loads(fetch("https://pypi.org/pypi/semantic-kernel/json", cache, "pypi_sk.json"))
    sk = con.sql(
        """
        SELECT tag_name FROM staging.stg_gh_releases
        WHERE repo = 'microsoft/semantic-kernel' AND tag_name LIKE 'python-%' AND NOT prerelease
        ORDER BY published_at DESC LIMIT 1
        """
    ).fetchone()
    sk_tag = sk[0] if sk else None
    out.append(
        {
            "repo": "microsoft/semantic-kernel",
            "github_latest_stable": sk_tag,
            "mart_latest_release_tag": rel.get("microsoft/semantic-kernel"),
            "published": f"PyPI semantic-kernel = {pypi['info']['version']}",
            "agree": sk_tag is not None
            and sk_tag.removeprefix("python-") == pypi["info"]["version"]
            and rel.get("microsoft/semantic-kernel") == sk_tag,
        }
    )
    page = fetch("https://code.visualstudio.com/updates", cache, "vscode_updates.html").decode(
        "utf-8", "ignore"
    )
    m = re.search(r"<title>\s*Visual Studio Code ([0-9.]+)", page)
    vs = rel.get("microsoft/vscode")
    out.append(
        {
            "repo": "microsoft/vscode",
            "github_latest_stable": vs,
            "published": f"code.visualstudio.com/updates title = {m.group(1) if m else '?'}",
            "agree": bool(m and vs and vs.startswith(m.group(1))),
        }
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--cache", type=Path, help="where downloads are kept (default <data_dir>/verify)"
    )
    ap.add_argument("--json", type=Path, help="also write the full result as JSON")
    args = ap.parse_args()
    settings = Settings.from_env()
    cache = args.cache or settings.data_dir / "verify"
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    result = {
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "vm": verify_vm(con, json.loads(fetch(CALC["vm"], cache, "calc_vm.json"))),
        "functions": verify_functions(
            con, json.loads(fetch(CALC["functions"], cache, "calc_functions.json"))
        ),
        "storage": verify_storage(
            con, json.loads(fetch(CALC["storage"], cache, "calc_storage.json"))
        ),
        "roadmap": verify_roadmap(con, fetch(RSS, cache, "roadmap_rss.xml")),
        "github": verify_github(con, cache),
    }
    con.close()
    for key in ("vm", "functions", "storage"):
        r = result[key]
        print(
            f"- {key}: {r['matched']}/{r['compared']} match the pricing calculator; {len(r['mismatches'])} mismatches"
        )
        for mm in r["mismatches"][:10]:
            print(f"    mismatch: {mm}")
    rm = result["roadmap"]
    print(
        f"- roadmap: JSON {rm['json_features']} features, RSS {rm['rss_items']} items; in both {rm['in_both']}, "
        f"only JSON {rm['only_json']}, only RSS {rm['only_rss']}; status agrees {rm['status_agree']}/{rm['status_compared']}; "
        f"GA date agrees {rm['ga_agree']}/{rm['ga_compared']}"
    )
    for g in result["github"]:
        extra = (
            f" (mart_repo_summary.latest_release_tag = {g['mart_latest_release_tag']})"
            if "mart_latest_release_tag" in g
            else ""
        )
        print(
            f"- {g['repo']}: GitHub {g['github_latest_stable']} vs {g['published']} -> {'agree' if g['agree'] else 'DIFFER'}{extra}"
        )
    if args.json:
        args.json.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
