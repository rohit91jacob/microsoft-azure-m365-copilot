"""Extractors: one per public source. Each lands raw snapshots in bronze and is idempotent."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from azm365.http import Fetcher
from azm365.lake import Lake

log = logging.getLogger(__name__)


@dataclass
class ExtractContext:
    lake: Lake
    fetcher: Fetcher
    config: dict[str, Any]
    as_of: datetime
    synthetic: bool = False
    force: bool = False
    max_pages: int | None = None
    github_token: str | None = field(default=None, repr=False)
    sleep: Callable[[float], None] = time.sleep

    @property
    def day_start(self) -> datetime:
        """Live snapshots are stamped at 00:00 UTC of the as-of day: one per scope per day."""
        d = self.as_of.astimezone(UTC)
        return datetime(d.year, d.month, d.day, tzinfo=UTC)


def run_extract(source: str, ctx: ExtractContext) -> list[dict[str, Any]]:
    from azm365.extract import github, prices, roadmap, wayback

    runners: dict[str, Callable[[ExtractContext], list[dict[str, Any]]]] = {
        "azure_prices": prices.extract,
        "m365_roadmap": roadmap.extract,
        "github": github.extract,
        "wayback": wayback.extract,
    }
    if source not in runners:
        raise ValueError(f"unknown source {source!r}; choose from {sorted(runners)}")
    started = time.monotonic()
    landed = runners[source](ctx)
    log.info(
        "extract %s: %d new snapshot(s), %d item(s) in %.1fs",
        source,
        len(landed),
        sum(r["item_count"] for r in landed),
        time.monotonic() - started,
    )
    return landed


SOURCES = ("azure_prices", "m365_roadmap", "github", "wayback")
