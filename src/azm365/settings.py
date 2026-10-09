"""Runtime settings, read from the environment (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]

LAKE_KINDS = ("local", "azurite", "adls")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


@dataclass(frozen=True)
class Settings:
    """Where data lives and how sources are reached.

    ``lake`` selects the storage backend for the bronze/silver/gold/ops containers:

    * ``local``   - a directory tree under ``data_dir/lake`` (default, no services needed)
    * ``azurite`` - the Azurite Blob emulator (``npx azurite``) via a connection string
    * ``adls``    - a real ADLS Gen2 account via ``DefaultAzureCredential`` (the Function App's
      managed identity). Never used by tests or CI.
    """

    data_dir: Path
    lake: str = "local"
    blob_connection_string: str | None = field(default=None, repr=False)
    storage_account_url: str | None = None
    github_token: str | None = field(default=None, repr=False)
    config_path: Path = REPO_ROOT / "config" / "sources.yml"
    dbt_project_dir: Path = REPO_ROOT / "dbt"
    http_timeout: float = 60.0
    user_agent: str = (
        "azm365-analytics/0.1 (+https://github.com/rohit91jacob/microsoft-azure-m365-copilot)"
    )

    @property
    def lake_root(self) -> Path:
        return self.data_dir / "lake"

    @property
    def silver_mirror(self) -> Path:
        """Local Parquet mirror of the silver container; DuckDB/dbt read from here."""
        return self.data_dir / "silver"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse" / "azm365.duckdb"

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        lake = (_env("AZM365_LAKE", "local") or "local").lower()
        if lake not in LAKE_KINDS:
            raise ValueError(f"AZM365_LAKE must be one of {LAKE_KINDS}, got {lake!r}")
        data_dir = Path(_env("AZM365_DATA_DIR", str(REPO_ROOT / "data")) or "data").expanduser()
        config = _env("AZM365_CONFIG")
        project = _env("AZM365_DBT_PROJECT_DIR")
        values = {
            "data_dir": data_dir.resolve(),
            "lake": lake,
            "blob_connection_string": _env("AZM365_BLOB_CONNECTION_STRING"),
            "storage_account_url": _env("AZM365_STORAGE_ACCOUNT_URL"),
            "github_token": _env("GITHUB_TOKEN"),
            "config_path": Path(config) if config else REPO_ROOT / "config" / "sources.yml",
            "dbt_project_dir": Path(project) if project else REPO_ROOT / "dbt",
            "http_timeout": float(_env("AZM365_HTTP_TIMEOUT", "60") or 60),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)
