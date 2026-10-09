"""Generate the committed HTTP fixtures under tests/fixtures/http/. SIMULATED DATA.

The Azure Retail Prices API, the Microsoft 365 roadmap feed and GitHub API responses are not
published under a licence that allows redistribution, so the repository does not commit real
responses. Instead this script stands up a *simulated* version of each API with the same
response shapes (field names, pagination, OData filters, Wayback CDX/replay, GitHub 202 stats),
fills it with deterministic, seeded fake values, and drives the real extractors against it,
recording every response under the request key the :class:`azm365.http.FixtureFetcher` uses.

Everything generated is fake: roadmap titles start with ``[SIMULATED]``, prices and GitHub
counts are pseudo-random. Real data is only ever downloaded at runtime (``azm365 run``).

Usage::

    uv run python scripts/make_fixtures.py            # rewrite tests/fixtures/http
    uv run python scripts/make_fixtures.py --check    # fail if the committed files are stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from azm365.extract import ExtractContext, run_extract  # noqa: E402
from azm365.http import Response, request_key  # noqa: E402
from azm365.lake import FileLake  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "http"
SEED = "azm365-fixtures-v1"
# Two consecutive daily runs. The simulated APIs change a little between them.
DAYS = ("2026-01-05", "2026-01-06")
# Simulated Internet Archive captures (all before the first day).
ROADMAP_CAPTURES = ("20251103101500", "20251201093000")
PRICE_CAPTURES = ("20251020120000",)
PAGE_SIZE = 20


def unit(*parts: Any) -> float:
    """Deterministic pseudo-random number in [0, 1) from the seed and ``parts``."""
    raw = "|".join([SEED, *map(str, parts)]).encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") / 2**64


def fake_id(*parts: Any) -> str:
    h = hashlib.sha256("|".join([SEED, *map(str, parts)]).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# --------------------------------------------------------------------------- Retail Prices

REGIONS = {
    # armRegionName: (location, price multiplier)
    "eastus": ("US East", 1.00),
    "westus2": ("US West 2", 1.00),
    "northeurope": ("EU North", 1.06),
    "westeurope": ("EU West", 1.12),
    "swedencentral": ("Sweden Central", 1.04),
    "spaincentral": ("Spain Central", 1.08),
    "centralindia": ("IN Central", 0.94),
    "usgovvirginia": ("US Gov Virginia", 1.25),  # excluded from rankings by the seed
}
VM_SKUS = {
    # armSkuName: (series product, meter, hourly base, regions offered)
    "Standard_D2s_v5": ("Dsv5 Series", "D2s v5", 0.096, None),
    "Standard_D4s_v5": ("Dsv5 Series", "D4s v5", 0.192, None),
    "Standard_E4s_v5": ("Esv5 Series", "E4s v5", 0.252, None),
    "Standard_F4s_v2": ("FSv2 Series", "F4s v2", 0.169, None),
    "Standard_B2s": ("BS Series", "B2s", 0.0416, None),
    "Standard_NC24ads_A100_v4": (
        "NCadsA100v4 Series",
        "NC24ads A100 v4",
        3.673,
        ("eastus", "westeurope", "swedencentral", "centralindia"),
    ),
}


def _price_item(
    *,
    service: str,
    family: str,
    product: str,
    sku: str,
    meter: str,
    region: str,
    price: float,
    unit_of_measure: str,
    price_type: str = "Consumption",
    term: str | None = None,
    tier: float = 0.0,
    arm_sku: str = "",
    effective: str = "2025-06-01T00:00:00Z",
) -> dict[str, Any]:
    location, _ = REGIONS.get(region, (region, 1.0))
    item = {
        "currencyCode": "USD",
        "tierMinimumUnits": tier,
        "retailPrice": round(price, 6),
        "unitPrice": round(price, 6),
        "armRegionName": region,
        "location": location,
        "effectiveStartDate": effective,
        "meterId": fake_id("meter", service, product, meter, region, tier),
        "meterName": meter,
        "productId": "SIM"
        + hashlib.sha1(product.encode(), usedforsecurity=False).hexdigest()[:9].upper(),
        "skuId": "SIM"
        + hashlib.sha1(f"{product}/{sku}".encode(), usedforsecurity=False).hexdigest()[:12].upper(),
        "productName": product,
        "skuName": sku,
        "serviceName": service,
        "serviceId": "SIM"
        + hashlib.sha1(service.encode(), usedforsecurity=False).hexdigest()[:9].upper(),
        "serviceFamily": family,
        "unitOfMeasure": unit_of_measure,
        "type": price_type,
        "isPrimaryMeterRegion": True,
        "armSkuName": arm_sku,
    }
    if term:
        item["reservationTerm"] = term
    return item


def vm_items(arm_sku: str) -> list[dict[str, Any]]:
    series, meter, base, offered = VM_SKUS[arm_sku]
    out = []
    for region, (_, mult) in REGIONS.items():
        if offered and region not in offered:
            continue
        jitter = 1 + (unit("vm", arm_sku, region) - 0.5) * 0.04
        linux = base * mult * jitter
        common = {
            "service": "Virtual Machines",
            "family": "Compute",
            "region": region,
            "arm_sku": arm_sku,
            "unit_of_measure": "1 Hour",
        }
        out += [
            _price_item(
                **common,
                product=f"Virtual Machines {series} Linux",
                sku=meter,
                meter=meter,
                price=linux,
            ),
            _price_item(
                **common,
                product=f"Virtual Machines {series} Windows",
                sku=meter,
                meter=meter,
                price=linux * 1.84,
            ),
            _price_item(
                **common,
                product=f"Virtual Machines {series} Linux",
                sku=f"{meter} Spot",
                meter=f"{meter} Spot",
                price=linux * 0.18,
            ),
            _price_item(
                **common,
                product=f"Virtual Machines {series} Windows",
                sku=meter,
                meter=meter,
                price=linux * 1.2,
                price_type="DevTestConsumption",
            ),
            _price_item(
                **common,
                product=f"Virtual Machines {series} Linux",
                sku=meter,
                meter=meter,
                price=linux * 8760 * 0.62,
                price_type="Reservation",
                term="1 Year",
            ),
            _price_item(
                **common,
                product=f"Virtual Machines {series} Linux",
                sku=meter,
                meter=meter,
                price=linux * 26280 * 0.42,
                price_type="Reservation",
                term="3 Years",
            ),
        ]
    return out


def blob_items() -> list[dict[str, Any]]:
    out = []
    for region, (_, mult) in REGIONS.items():
        common = {
            "service": "Storage",
            "family": "Storage",
            "product": "General Block Blob v2",
            "region": region,
        }
        for tier, price in ((0.0, 0.0184), (51200.0, 0.0177), (512000.0, 0.0170)):
            out.append(
                _price_item(
                    **common,
                    sku="Hot LRS",
                    meter="Hot LRS Data Stored",
                    price=price * mult,
                    unit_of_measure="1 GB/Month",
                    tier=tier,
                )
            )
        out += [
            _price_item(
                **common,
                sku="Cool LRS",
                meter="Cool LRS Data Stored",
                price=0.01 * mult,
                unit_of_measure="1 GB/Month",
            ),
            _price_item(
                **common,
                sku="Hot LRS",
                meter="Hot LRS Write Operations",
                price=0.05 * mult,
                unit_of_measure="10K",
            ),
            _price_item(
                **common,
                sku="Hot LRS",
                meter="Read Operations",
                price=0.004 * mult,
                unit_of_measure="10K",
            ),
        ]
    return out


def functions_items() -> list[dict[str, Any]]:
    out = []
    for region in ("eastus", "westeurope", "northeurope", "centralindia", "swedencentral"):
        mult = REGIONS[region][1]
        common = {
            "service": "Functions",
            "family": "Compute",
            "product": "Functions",
            "sku": "Standard",
            "region": region,
        }
        out += [
            _price_item(
                **common, meter="Standard Execution Time", price=0.0, unit_of_measure="1 GB Second"
            ),
            _price_item(
                **common,
                meter="Standard Execution Time",
                price=0.000016 * mult,
                unit_of_measure="1 GB Second",
                tier=400000.0,
            ),
            _price_item(
                **common, meter="Standard Total Executions", price=0.0, unit_of_measure="10"
            ),
            _price_item(
                **common,
                meter="Standard Total Executions",
                price=0.000002 * mult,
                unit_of_measure="10",
                tier=100000.0,
            ),
            _price_item(
                **{**common, "sku": "Flex Consumption", "product": "Functions Flex Consumption"},
                meter="On Demand Execution Time",
                price=0.000026 * mult,
                unit_of_measure="1 GB Second",
            ),
        ]
    return out


def foundry_items() -> list[dict[str, Any]]:
    models = {"sim-gpt-mini": 0.00015, "sim-gpt-large": 0.0025, "sim-embed-small": 0.00002}
    out = []
    for model, inp in models.items():
        for region in ("eastus", "swedencentral", "westeurope", "Global"):
            mult = 1.0 if region == "Global" else 1.1
            common = {
                "service": "Foundry Models",
                "family": "AI + Machine Learning",
                "product": f"Azure OpenAI {model}",
                "region": region,
                "unit_of_measure": "1K",
            }
            out += [
                _price_item(
                    **common, sku=f"{model} Inp", meter=f"{model} Input Tokens", price=inp * mult
                ),
                _price_item(
                    **common,
                    sku=f"{model} Outp",
                    meter=f"{model} Output Tokens",
                    price=inp * 4 * mult,
                ),
            ]
    return out


def region_items(region: str) -> list[dict[str, Any]]:
    items = [i for sku in VM_SKUS for i in vm_items(sku) if i["armRegionName"] == region]
    items += [i for i in blob_items() + functions_items() if i["armRegionName"] == region]
    return items


def apply_version(items: list[dict[str, Any]], version: int) -> list[dict[str, Any]]:
    """Version 0 = archived capture, 1 = first live day, 2 = second live day.

    Each step reprices ~15% of the price points by -20%..+20% with a new effective date.
    """
    out = []
    for it in items:
        price = it["retailPrice"]
        effective = it["effectiveStartDate"]
        for v in range(1, version + 1):
            if (
                unit("reprice", it["meterId"], it["type"], it.get("reservationTerm"), v) < 0.15
                and price > 0
            ):
                price *= 1 + (unit("delta", it["meterId"], v) - 0.5) * 0.4
                effective = f"2026-01-0{v + 2}T00:00:00Z"
        out.append(
            {
                **it,
                "retailPrice": round(price, 6),
                "unitPrice": round(price, 6),
                "effectiveStartDate": effective,
            }
        )
    return out


def items_for_filter(odata_filter: str) -> list[dict[str, Any]]:
    clauses = dict(re.findall(r"(\w+) eq '([^']*)'", odata_filter))
    if clauses.get("armSkuName") in VM_SKUS:
        return vm_items(clauses["armSkuName"])
    if clauses.get("productName") == "General Block Blob v2":
        return blob_items()
    if clauses.get("serviceName") == "Functions":
        return functions_items()
    if clauses.get("serviceName") == "Foundry Models":
        return foundry_items()
    if "armRegionName" in clauses:
        return region_items(clauses["armRegionName"])
    return []


def prices_page(query: dict[str, str], version: int) -> dict[str, Any]:
    odata_filter = query["$filter"]
    skip = int(query.get("$skip", "0"))
    items = apply_version(items_for_filter(odata_filter), version)
    page = items[skip : skip + PAGE_SIZE]
    next_link = None
    if skip + PAGE_SIZE < len(items):
        nxt = {k: v for k, v in query.items() if k != "$skip"} | {"$skip": str(skip + PAGE_SIZE)}
        next_link = "https://prices.azure.com:443/api/retail/prices?" + urlencode(nxt)
    return {
        "BillingCurrency": "USD",
        "CustomerEntityId": "Default",
        "CustomerEntityType": "Retail",
        "Items": page,
        "NextPageLink": next_link,
        "Count": len(page),
    }


# --------------------------------------------------------------------------- M365 roadmap

PRODUCTS = [
    "Microsoft Copilot (Microsoft 365)",
    "Microsoft Teams",
    "Outlook",
    "SharePoint",
    "Word",
    "Excel",
    "PowerPoint",
    "Microsoft Purview compliance portal",  # renamed; mapped by the alias seed
    "Microsoft Viva",
]
STATUSES = ("In development", "Rolling out", "Launched")
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _ga_label(month_index: int) -> str:
    """month_index 0 = January 2026."""
    y, m = divmod(month_index, 12)
    return f"{MONTH_NAMES[m]} CY{2026 + y}"


def roadmap_items(version: int) -> list[dict[str, Any]]:
    """version 0..3 = two archived captures, then the two live days."""
    snapshot_day = [date(2025, 11, 3), date(2025, 12, 1), date(2026, 1, 5), date(2026, 1, 6)][
        version
    ]
    out = []
    for n in range(60):
        added_in = 0 if n < 36 else (1 if n < 48 else (2 if n < 56 else 3))
        if added_in > version:
            continue
        fid = 500000 + n * 7
        products = [PRODUCTS[n % len(PRODUCTS)]]
        if n % 4 == 0 and products[0] != PRODUCTS[0]:
            products.append(PRODUCTS[0])  # a Copilot feature inside another product
        ga = int(unit("ga", fid) * 12)
        moves = sum(
            (1 if unit("slip", fid, v) < 0.8 else -1)
            for v in range(1, version + 1)
            if unit("move", fid, v) < 0.25
        )
        status_i = min(2, int(unit("status", fid) * 2) + version // 2)
        created = datetime(2025, 9, 1, tzinfo=UTC) + timedelta(
            days=int(unit("created", fid) * 60) + 30 * added_in
        )
        created = min(created, datetime.combine(snapshot_day, datetime.min.time(), UTC))
        container = {
            "products": [{"tagName": p} for p in products],
            "cloudInstances": [{"tagName": "Worldwide (Standard Multi-Tenant)"}],
            "releasePhase": [{"tagName": "General Availability"}],
            "platforms": [{"tagName": "Web"}, {"tagName": "Desktop"}][: 1 + n % 2],
        }
        out.append(
            {
                "id": fid,
                "title": f"[SIMULATED] {products[0]}: simulated feature {n:02d}",
                "description": "<p>Simulated roadmap item for tests. Not a real Microsoft feature.</p>",
                "moreInfoLink": None,
                "publicDisclosureAvailabilityDate": _ga_label(ga + moves) if n % 10 else "",
                "publicPreviewDate": _ga_label(ga + moves - 2) if n % 3 == 0 else "",
                "created": created.strftime("%Y-%m-%dT%H:%M:%S"),
                "publicRoadmapStatus": "Include this month",
                "status": STATUSES[status_i],
                "modified": datetime.combine(snapshot_day, datetime.min.time()).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                ),
                "locale": None,
                "tags": [{"tagName": t["tagName"]} for k in container for t in container[k]],
                "tagsContainer": container,
            }
        )
    return out


# --------------------------------------------------------------------------- GitHub


def gh_repo(repo: str) -> dict[str, Any]:
    u = unit("repo", repo)
    return {
        "full_name": repo,
        "created_at": "2015-09-03T20:23:38Z",
        "pushed_at": "2026-01-05T08:00:00Z",
        "stargazers_count": int(10000 + u * 90000),
        "forks_count": int(1000 + u * 9000),
        "open_issues_count": int(100 + u * 900),
        "subscribers_count": int(200 + u * 800),
        "default_branch": "main",
        "language": "TypeScript",
        "archived": False,
        "size": 123456,
        "license": {"spdx_id": "MIT"},
    }


def gh_releases(repo: str, day: date) -> list[dict[str, Any]]:
    out = []
    for i in range(8):
        published = datetime.combine(day, datetime.min.time(), UTC) - timedelta(days=5 + 30 * i)
        out.append(
            {
                "id": int(unit("rel", repo, i) * 1e9),
                "tag_name": f"v1.{20 - i}.0",
                "name": f"Simulated release 1.{20 - i}",
                "draft": False,
                "prerelease": i % 4 == 3,
                "created_at": (published - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                "published_at": published.isoformat().replace("+00:00", "Z"),
                "assets": [{"id": 1}] * (i % 3),
                "body": "simulated",
            }
        )
    return out


def gh_pulls(repo: str, day: date) -> list[dict[str, Any]]:
    out = []
    end = datetime.combine(day, datetime.min.time(), UTC)
    for i in range(40):
        created = end - timedelta(hours=6 + i * 9 + unit("pr", repo, i) * 4)
        merged = unit("merged", repo, i) < 0.75
        closed = created + timedelta(hours=1 + unit("ttm", repo, i) * 60)
        closed = min(closed, end - timedelta(minutes=5))
        bot = unit("bot", repo, i) < 0.15
        out.append(
            {
                "number": 9000 + i,
                "state": "closed",
                "draft": False,
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "closed_at": closed.isoformat().replace("+00:00", "Z"),
                "merged_at": closed.isoformat().replace("+00:00", "Z") if merged else None,
                "updated_at": (closed + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "base": {"ref": "main"},
                "user": {
                    "login": "dependabot[bot]" if bot else f"sim-user-{i % 7}",
                    "type": "Bot" if bot else "User",
                },
                "author_association": "CONTRIBUTOR" if i % 3 else "MEMBER",
                "body": "simulated",
            }
        )
    return sorted(out, key=lambda p: p["updated_at"], reverse=True)


def gh_participation(repo: str) -> dict[str, list[int]]:
    weekly = [int(unit("commits", repo, w) * 80) for w in range(52)]
    return {"all": weekly, "owner": [c // 3 for c in weekly]}


# --------------------------------------------------------------------------- simulated server


class SimulatedAPIs:
    """Routes a GET to the simulated Retail Prices, roadmap, GitHub or Wayback endpoint."""

    def __init__(self, day_index: int, config: dict[str, Any]) -> None:
        self.day_index = day_index
        self.day = date.fromisoformat(DAYS[day_index])
        self.config = config
        self.calls: dict[str, bytes] = {}

    def _body(self, url: str, params: dict[str, str] | None) -> tuple[int, Any]:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query)) | (params or {})
        host, path = parts.hostname, parts.path
        if host == "prices.azure.com":
            return 200, prices_page(query, version=1 + self.day_index)
        if host == "www.microsoft.com" and path.endswith("/api/v1/m365"):
            return 200, roadmap_items(2 + self.day_index)
        if host == "api.github.com":
            m = re.match(r"^/repos/([^/]+/[^/]+)(/.*)?$", path)
            if not m:
                raise KeyError(url)
            repo, rest = m.group(1), m.group(2) or ""
            if rest == "":
                return 200, gh_repo(repo)
            if rest == "/releases":
                return 200, gh_releases(repo, self.day) if query.get("page") == "1" else []
            if rest == "/pulls":
                return 200, gh_pulls(repo, self.day) if query.get("page") == "1" else []
            if rest == "/stats/participation":
                return 200, gh_participation(repo)
        if host == "web.archive.org" and path == "/cdx/search/cdx":
            original = query["url"]
            captures = PRICE_CAPTURES if "prices.azure.com" in original else ROADMAP_CAPTURES
            header = [
                "urlkey",
                "timestamp",
                "original",
                "mimetype",
                "statuscode",
                "digest",
                "length",
            ]
            rows = [
                [original, ts, original, "application/json", "200", f"SIMDIGEST{ts}", "1000"]
                for ts in captures
            ]
            return 200, [header, *rows]
        if host == "web.archive.org" and path.startswith("/web/"):
            m = re.match(r"^/web/(\d{14})id_/(.*)$", url.split("web.archive.org", 1)[1])
            if not m:
                raise KeyError(url)
            ts, original = m.groups()
            if "prices.azure.com" in original:
                orig = urlsplit(original)
                return 200, prices_page(dict(parse_qsl(orig.query)), version=0)
            return 200, roadmap_items(ROADMAP_CAPTURES.index(ts))
        raise KeyError(f"simulated APIs have no route for {url}")

    def get(self, url, params=None, headers=None, *, accept_status=(200,)) -> Response:
        status, body = self._body(url, params)
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        self.calls[request_key(url, params)] = data
        return Response(status, data, {})


def generate(out: Path) -> dict[str, int]:
    config = yaml.safe_load((REPO / "config" / "sources.yml").read_text(encoding="utf-8"))
    recorded: list[dict[str, bytes]] = []
    for i, day in enumerate(DAYS):
        api = SimulatedAPIs(i, config)
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ExtractContext(
                lake=FileLake(Path(tmp)),
                fetcher=api,
                config=config,
                as_of=datetime.fromisoformat(day).replace(hour=6, tzinfo=UTC),
                synthetic=True,
                sleep=lambda _s: None,
            )
            for source in ("azure_prices", "m365_roadmap", "github", "wayback"):
                run_extract(source, ctx)
        recorded.append(api.calls)
    if out.exists():
        shutil.rmtree(out)
    counts: dict[str, int] = {}
    common_keys = {
        k for k in recorded[0] if all(k in r and r[k] == recorded[0][k] for r in recorded[1:])
    }
    for key in sorted(common_keys):
        _write(out / "common" / f"{key}.json", recorded[0][key])
        counts["common"] = counts.get("common", 0) + 1
    for day, calls in zip(DAYS, recorded, strict=True):
        for key, data in sorted(calls.items()):
            if key in common_keys:
                continue
            _write(out / day / f"{key}.json", data)
            counts[day] = counts.get(day, 0) + 1
    (out / "README.md").write_text(
        "# SIMULATED HTTP fixtures\n\n"
        "Every file here is **simulated** API output generated by `scripts/make_fixtures.py` "
        f"(seed `{SEED}`): the response *shapes* of the Azure Retail Prices API, the Microsoft 365 "
        "roadmap feed, the GitHub REST API and the Internet Archive CDX/replay endpoints, filled "
        "with deterministic fake values. None of it is real Microsoft, Azure or GitHub data.\n\n"
        "File names are `azm365.http.request_key(url, params)`; `<day>/` holds responses that "
        "differ between the two simulated days, `common/` the ones that do not. Regenerate with "
        "`make fixtures` after changing `config/sources.yml`.\n",
        encoding="utf-8",
        newline="\n",
    )
    return counts


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data + b"\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if committed fixtures are stale")
    args = ap.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            generate(Path(tmp) / "http")
            fresh = {
                p.relative_to(Path(tmp) / "http"): p.read_bytes()
                for p in (Path(tmp) / "http").rglob("*")
                if p.is_file()
            }
        current = {p.relative_to(OUT): p.read_bytes() for p in OUT.rglob("*") if p.is_file()}
        if fresh != current:
            print("tests/fixtures/http is stale: run `make fixtures`")
            return 1
        print(f"fixtures up to date ({len(current)} files)")
        return 0
    counts = generate(OUT)
    largest = max(p.stat().st_size for p in OUT.rglob("*.json"))
    print(json.dumps({"files": counts, "largest_bytes": largest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
