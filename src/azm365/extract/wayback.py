"""Real history from the Internet Archive's Wayback Machine.

Neither the Retail Prices API nor the roadmap feed keeps history, but the Internet Archive has
captured both endpoints over time. The CDX index lists every distinct (``collapse=digest``)
successful capture of a URL; each is replayed raw (``id_``) and landed in bronze exactly like a
live snapshot, stamped with its capture time and ``origin=wayback``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from azm365 import bronze
from azm365.extract import ExtractContext

log = logging.getLogger(__name__)


def list_captures(ctx: ExtractContext, target: dict[str, Any]) -> list[tuple[str, str]]:
    cfg = ctx.config["wayback"]
    params = {
        "url": target["url"],
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "digest",
        "from": str(target.get("from", "2024")),
    }
    rows = ctx.fetcher.get(cfg["cdx"], params).json()
    if not rows:
        return []
    header, *data = rows
    ts_i, orig_i = header.index("timestamp"), header.index("original")
    return [(r[ts_i], r[orig_i]) for r in data]


def _payload(source: str, body: Any) -> tuple[dict[str, Any], int, bool]:
    if source == "m365_roadmap":
        if not isinstance(body, list):
            raise ValueError("archived roadmap capture is not a JSON array")
        return {"items": body}, len(body), True
    if source == "azure_prices":
        items = body["Items"]
        # Only the requested page was archived: never treat it as the full catalogue.
        return {"items": items, "pages": 1}, len(items), not body.get("NextPageLink")
    raise ValueError(f"no wayback handler for source {source!r}")


def extract(ctx: ExtractContext) -> list[dict[str, Any]]:
    cfg = ctx.config["wayback"]
    landed = []
    for target in cfg["targets"]:
        for timestamp, original in list_captures(ctx, target):
            ts = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            if ts > ctx.as_of:
                continue  # replaying history "as of" a date never looks into its future
            ref = bronze.SnapshotRef(
                target["source"],
                target["dataset"],
                target["scope"],
                bronze.snapshot_id(ts, "wayback"),
            )
            if not ctx.force and bronze.exists(ctx.lake, ref):
                continue
            replay = f"{cfg['replay']}/{timestamp}id_/{original}"
            body = ctx.fetcher.get(replay).json()
            payload, n, complete = _payload(target["source"], body)
            record = bronze.write_snapshot(
                ctx.lake,
                ref,
                payload,
                item_count=n,
                complete=complete,
                request={"replay": replay, "original": original},
                synthetic=ctx.synthetic,
                force=ctx.force,
            )
            if record:
                log.info(
                    "wayback %s/%s @ %s: %d items", target["source"], target["scope"], timestamp, n
                )
                landed.append(record)
    return landed
