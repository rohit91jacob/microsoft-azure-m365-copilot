"""Microsoft 365 public roadmap: the full feature list, one bronze snapshot per day."""

from __future__ import annotations

import logging
from typing import Any

from azm365 import bronze
from azm365.extract import ExtractContext

log = logging.getLogger(__name__)

SOURCE, DATASET, SCOPE = "m365_roadmap", "features", "all"


def extract(ctx: ExtractContext) -> list[dict[str, Any]]:
    cfg = ctx.config["m365_roadmap"]
    ref = bronze.SnapshotRef(SOURCE, DATASET, SCOPE, bronze.snapshot_id(ctx.day_start, "live"))
    if not ctx.force and bronze.exists(ctx.lake, ref):
        log.info("roadmap: %s already landed, skipping", ref.snapshot_id)
        return []
    items = ctx.fetcher.get(cfg["endpoint"]).json()
    if not isinstance(items, list):
        raise TypeError("roadmap endpoint did not return a JSON array")
    record = bronze.write_snapshot(
        ctx.lake,
        ref,
        {"items": items},
        item_count=len(items),
        complete=True,
        request={"url": cfg["endpoint"]},
        synthetic=ctx.synthetic,
        force=ctx.force,
    )
    return [record] if record else []
