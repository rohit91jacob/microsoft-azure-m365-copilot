"""Shared fixtures. Everything runs offline against the SIMULATED HTTP fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from azm365.settings import REPO_ROOT, Settings

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "http"
DAY1, DAY2 = "2026-01-05", "2026-01-06"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for var in ("AZM365_LAKE", "AZM365_BLOB_CONNECTION_STRING", "GITHUB_TOKEN", "AZM365_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DBT_TARGET", "local")
    return Settings.from_env(data_dir=tmp_path / "data")


@pytest.fixture(scope="session")
def pipeline_runs(tmp_path_factory: pytest.TempPathFactory):
    """Two simulated daily runs of the whole pipeline into one data dir (session-scoped)."""
    from azm365 import pipeline

    data_dir = tmp_path_factory.mktemp("e2e") / "data"
    settings = Settings(data_dir=data_dir)
    runs = [pipeline.run_all(settings, as_of=day, fixtures=FIXTURES) for day in (DAY1, DAY2)]
    return settings, runs
