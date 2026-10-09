"""Bronze: immutable, gzip'd JSON envelopes around raw API payloads, plus a manifest.

Layout in the ``bronze`` container::

    <source>/<dataset>/<scope>/snapshot_date=YYYY-MM-DD/<snapshot_id>.json.gz
    _manifest/<source>/<dataset>/<scope>/<snapshot_id>.json

``snapshot_id`` is ``<YYYYMMDDTHHMMSSZ>-<origin>``. Live snapshots are stamped at the start of
the UTC day they were taken for (one per scope per day), Wayback snapshots at their capture
time. Re-running an extract for the same day therefore targets the same object and is skipped,
which makes every extractor idempotent; ``--force`` re-fetches and overwrites.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from azm365.lake import Lake

ORIGINS = ("live", "wayback")


def snapshot_id(ts: datetime, origin: str) -> str:
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}")
    return ts.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{origin}"


def parse_snapshot_id(sid: str) -> tuple[datetime, str]:
    stamp, origin = sid.split("-", 1)
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), origin


@dataclass(frozen=True)
class SnapshotRef:
    source: str
    dataset: str
    scope: str
    snapshot_id: str

    @property
    def snapshot_ts(self) -> datetime:
        return parse_snapshot_id(self.snapshot_id)[0]

    @property
    def origin(self) -> str:
        return parse_snapshot_id(self.snapshot_id)[1]

    @property
    def path(self) -> str:
        day = self.snapshot_ts.strftime("%Y-%m-%d")
        return f"{self.source}/{self.dataset}/{self.scope}/snapshot_date={day}/{self.snapshot_id}.json.gz"

    @property
    def manifest_path(self) -> str:
        return f"_manifest/{self.source}/{self.dataset}/{self.scope}/{self.snapshot_id}.json"


def encode(envelope: dict[str, Any]) -> bytes:
    raw = json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # mtime=0 keeps the gzip bytes deterministic, so identical payloads hash identically.
    return gzip.compress(raw.encode("utf-8"), mtime=0)


def decode(blob: bytes) -> dict[str, Any]:
    return json.loads(gzip.decompress(blob).decode("utf-8"))


def exists(lake: Lake, ref: SnapshotRef) -> bool:
    return lake.exists("bronze", ref.manifest_path)


def write_snapshot(
    lake: Lake,
    ref: SnapshotRef,
    payload: dict[str, Any],
    *,
    item_count: int,
    complete: bool,
    request: dict[str, Any],
    synthetic: bool = False,
    force: bool = False,
) -> dict[str, Any] | None:
    """Land one snapshot. Returns the manifest record, or None when it was already landed."""
    if not force and exists(lake, ref):
        return None
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    envelope = {
        "source": ref.source,
        "dataset": ref.dataset,
        "scope": ref.scope,
        "snapshot_id": ref.snapshot_id,
        "origin": ref.origin,
        "synthetic": synthetic,
        "request": request,
        "payload": payload,
    }
    data = encode(envelope)
    record = {
        **asdict(ref),
        "path": ref.path,
        "origin": ref.origin,
        "snapshot_ts": ref.snapshot_ts.isoformat(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "item_count": item_count,
        "complete": complete,
        "synthetic": synthetic,
        "fetched_at": fetched_at,
        "request": request,
    }
    # Object first, manifest second: a manifest entry always points at a fully written object.
    lake.put("bronze", ref.path, data, overwrite=True)
    lake.put("bronze", ref.manifest_path, json.dumps(record, indent=1).encode(), overwrite=True)
    return record


def list_manifests(lake: Lake, source: str | None = None) -> list[dict[str, Any]]:
    prefix = f"_manifest/{source}/" if source else "_manifest/"
    out = [json.loads(lake.get("bronze", p)) for p in lake.list("bronze", prefix)]
    return sorted(out, key=lambda r: (r["source"], r["dataset"], r["scope"], r["snapshot_id"]))


def read_snapshot(lake: Lake, record: dict[str, Any]) -> dict[str, Any]:
    blob = lake.get("bronze", record["path"])
    if hashlib.sha256(blob).hexdigest() != record["sha256"]:
        raise ValueError(f"bronze object {record['path']} does not match its manifest sha256")
    return decode(blob)
