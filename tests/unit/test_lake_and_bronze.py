from __future__ import annotations

from datetime import UTC, datetime

import pytest

from azm365 import bronze
from azm365.lake import FileLake, check_path, open_lake
from azm365.settings import Settings


@pytest.mark.parametrize("path", ["../x", "/abs", "a/../b", r"a\b"])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ValueError, match="unsafe"):
        check_path("bronze", path)


def test_unknown_container_rejected():
    with pytest.raises(ValueError, match="container"):
        check_path("platinum", "x")


def test_file_lake_put_get_list_and_no_overwrite(tmp_path):
    lake = FileLake(tmp_path)
    assert lake.put("silver", "a/b.txt", b"1")
    assert not lake.put("silver", "a/b.txt", b"2", overwrite=False)
    assert lake.get("silver", "a/b.txt") == b"1"
    assert lake.list("silver", "a/") == ["a/b.txt"]
    assert not list(tmp_path.rglob(".tmp-*"))


def test_open_lake_validates_settings(tmp_path):
    with pytest.raises(ValueError, match="CONNECTION_STRING"):
        open_lake(Settings(data_dir=tmp_path, lake="azurite"))
    with pytest.raises(ValueError, match="ACCOUNT_URL"):
        open_lake(Settings(data_dir=tmp_path, lake="adls"))


def test_settings_reject_unknown_lake(monkeypatch):
    monkeypatch.setenv("AZM365_LAKE", "s3")
    with pytest.raises(ValueError, match="AZM365_LAKE"):
        Settings.from_env()


def test_snapshot_id_roundtrip():
    ts = datetime(2026, 1, 5, 7, 8, 9, tzinfo=UTC)
    sid = bronze.snapshot_id(ts, "wayback")
    assert sid == "20260105T070809Z-wayback"
    assert bronze.parse_snapshot_id(sid) == (ts, "wayback")
    with pytest.raises(ValueError):
        bronze.snapshot_id(ts, "other")


def test_write_snapshot_is_idempotent_and_deterministic(tmp_path):
    lake = FileLake(tmp_path)
    ref = bronze.SnapshotRef("m365_roadmap", "features", "all", "20260105T000000Z-live")
    assert (
        ref.path
        == "m365_roadmap/features/all/snapshot_date=2026-01-05/20260105T000000Z-live.json.gz"
    )
    rec = bronze.write_snapshot(lake, ref, {"items": [1]}, item_count=1, complete=True, request={})
    assert rec and bronze.exists(lake, ref)
    assert (
        bronze.write_snapshot(lake, ref, {"items": [2]}, item_count=1, complete=True, request={})
        is None
    )
    again = bronze.write_snapshot(
        lake, ref, {"items": [1]}, item_count=1, complete=True, request={}, force=True
    )
    assert again["sha256"] == rec["sha256"]  # gzip mtime=0: same payload, same bytes
    assert bronze.read_snapshot(lake, rec)["payload"] == {"items": [1]}
    assert [m["snapshot_id"] for m in bronze.list_manifests(lake)] == [ref.snapshot_id]


def test_read_snapshot_detects_tampering(tmp_path):
    lake = FileLake(tmp_path)
    ref = bronze.SnapshotRef("github", "repo_activity", "o__r", "20260105T000000Z-live")
    rec = bronze.write_snapshot(lake, ref, {"x": 1}, item_count=0, complete=True, request={})
    lake.put("bronze", rec["path"], bronze.encode({"payload": {"x": 2}}))
    with pytest.raises(ValueError, match="sha256"):
        bronze.read_snapshot(lake, rec)
