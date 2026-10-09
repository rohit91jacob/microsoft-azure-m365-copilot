"""Azure Retail Prices API: paginated OData queries, one bronze snapshot per scope per day."""

from __future__ import annotations

import logging
from typing import Any

from azm365 import bronze
from azm365.extract import ExtractContext

log = logging.getLogger(__name__)

SOURCE, DATASET = "azure_prices", "retail_prices"


def _params(cfg: dict[str, Any], scope: dict[str, Any], odata_filter: str) -> dict[str, str]:
    params = {"$filter": odata_filter}
    api_version = scope.get("api_version", cfg.get("api_version"))
    if api_version:
        params["api-version"] = str(api_version)
    currency = cfg.get("currency", "USD")
    if currency != "USD":  # USD is the API default; omitting it keeps archived URLs comparable
        params["currencyCode"] = currency
    return params


def fetch_scope(ctx: ExtractContext, cfg: dict[str, Any], scope: dict[str, Any]):
    limit = scope.get("max_pages") or ctx.max_pages
    if ctx.max_pages and scope.get("max_pages"):
        limit = min(ctx.max_pages, scope["max_pages"])
    items: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    truncated = False
    pages = 0
    for odata_filter in scope["filters"]:
        url: str | None = cfg["endpoint"]
        params: dict[str, str] | None = _params(cfg, scope, odata_filter)
        requests.append({"url": url, "params": params})
        filter_pages = 0
        while url:
            body = ctx.fetcher.get(url, params).json()
            if "Items" not in body:
                raise ValueError(f"unexpected Retail Prices response for {odata_filter!r}")
            items.extend(body["Items"])
            pages += 1
            filter_pages += 1
            url, params = body.get("NextPageLink"), None
            if url and limit and filter_pages >= limit:
                truncated = True
                break
    return items, pages, not truncated, requests


def extract(ctx: ExtractContext) -> list[dict[str, Any]]:
    cfg = ctx.config["azure_prices"]
    landed = []
    for scope in cfg["scopes"]:
        ref = bronze.SnapshotRef(
            SOURCE, DATASET, scope["name"], bronze.snapshot_id(ctx.day_start, "live")
        )
        if not ctx.force and bronze.exists(ctx.lake, ref):
            log.info("prices/%s: %s already landed, skipping", scope["name"], ref.snapshot_id)
            continue
        items, pages, complete, requests = fetch_scope(ctx, cfg, scope)
        record = bronze.write_snapshot(
            ctx.lake,
            ref,
            {"items": items, "pages": pages},
            item_count=len(items),
            complete=complete,
            request={"requests": requests},
            synthetic=ctx.synthetic,
            force=ctx.force,
        )
        if record:
            log.info("prices/%s: %d items over %d pages", scope["name"], len(items), pages)
            landed.append(record)
    return landed
