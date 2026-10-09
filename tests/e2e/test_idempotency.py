"""Reruns: the same day twice, a forced re-fetch and a full silver refresh change nothing."""

from __future__ import annotations

import hashlib

import duckdb
import pytest

from azm365 import pipeline
from azm365.settings import Settings
from tests.conftest import DAY1, FIXTURES

pytestmark = pytest.mark.e2e


def mart_fingerprint(settings: Settings) -> dict[str, str]:
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        names = pipeline.mart_names(con)
        out = {}
        for name in names:
            rows = con.sql(f'SELECT * FROM marts."{name}" ORDER BY ALL').fetchall()
            out[name] = hashlib.sha256(repr(rows).encode()).hexdigest()
        return out
    finally:
        con.close()


def bronze_state(settings: Settings) -> dict[str, bytes]:
    root = settings.lake_root / "bronze"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.json.gz")}


def test_rerun_same_day_is_a_no_op(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    first = pipeline.run_all(settings, as_of=DAY1, fixtures=FIXTURES)
    marts, objects = mart_fingerprint(settings), bronze_state(settings)

    second = pipeline.run_all(settings, as_of=DAY1, fixtures=FIXTURES)
    assert second["landed"] == 0
    assert second["silver"] == {"built": 0, "skipped": first["silver"]["built"]}
    assert bronze_state(settings) == objects
    assert mart_fingerprint(settings) == marts

    # --force re-fetches and rewrites every snapshot, but identical payloads give identical
    # bytes (deterministic gzip), so silver sees the same sha256 and skips them all.
    forced = pipeline.extract(settings, as_of=DAY1, fixtures=FIXTURES, force=True)
    assert len(forced) == first["landed"]
    assert bronze_state(settings) == objects
    assert pipeline.build_silver(settings)["built"] == 0

    # Rebuilding silver from scratch reproduces exactly the same gold.
    assert pipeline.build_silver(settings, full_refresh=True)["built"] == first["silver"]["built"]
    pipeline.run_dq(settings)
    pipeline.run_dbt(settings)
    assert mart_fingerprint(settings) == marts
