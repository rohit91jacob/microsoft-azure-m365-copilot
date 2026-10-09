"""Pipeline steps shared by the CLI and the Azure Functions app.

extract (bronze) -> silver -> DQ gate -> dbt build (gold + tests) -> publish gold to the lake.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
import yaml

from azm365 import bronze, quality, silver
from azm365.extract import SOURCES, ExtractContext, run_extract
from azm365.http import Fetcher, FixtureFetcher, LiveFetcher
from azm365.lake import Lake, open_lake
from azm365.settings import Settings

log = logging.getLogger(__name__)

EXIT_DQ = 3
EXIT_DBT = 4


class DataQualityError(RuntimeError):
    def __init__(self, failed: list[quality.CheckResult]) -> None:
        super().__init__(
            "error-level data-quality checks failed: " + ", ".join(f.name for f in failed)
        )
        self.failed = failed


class TransformError(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # A bare date means "as of the end of that UTC day".
        dt = dt.replace(tzinfo=UTC)
        if len(value) == 10:
            dt = dt.replace(hour=23, minute=59, second=59)
    return dt.astimezone(UTC)


def make_fetcher(settings: Settings, fixtures: Path | None, as_of: datetime) -> Fetcher:
    if fixtures is not None:
        return FixtureFetcher(fixtures, as_of.strftime("%Y-%m-%d"))
    return LiveFetcher(user_agent=settings.user_agent, timeout=settings.http_timeout)


def extract(
    settings: Settings,
    sources: list[str] | None = None,
    *,
    as_of: str | None = None,
    fixtures: Path | None = None,
    force: bool = False,
    max_pages: int | None = None,
    lake: Lake | None = None,
) -> list[dict[str, Any]]:
    lake = lake or open_lake(settings)
    when = parse_as_of(as_of)
    ctx = ExtractContext(
        lake=lake,
        fetcher=make_fetcher(settings, fixtures, when),
        config=load_config(settings.config_path),
        as_of=when,
        synthetic=fixtures is not None,
        force=force,
        max_pages=max_pages,
        github_token=None if fixtures else settings.github_token,
        sleep=(lambda _s: None) if fixtures else time.sleep,
    )
    landed: list[dict[str, Any]] = []
    for source in sources or list(SOURCES):
        landed.extend(run_extract(source, ctx))
    return landed


def build_silver(settings: Settings, *, full_refresh: bool = False, lake: Lake | None = None):
    lake = lake or open_lake(settings)
    return silver.build(lake, full_refresh=full_refresh, mirror=silver.mirror_for(settings, lake))


def run_dq(settings: Settings, *, lake: Lake | None = None) -> list[quality.CheckResult]:
    lake = lake or open_lake(settings)
    silver_dir = silver.read_dir(settings, lake)
    silver.ensure_empty_tables(silver_dir)
    results = quality.run(silver_dir, manifests=bronze.list_manifests(lake))
    quality.record(lake, results)
    failed = quality.failed_errors(results)
    if failed:
        raise DataQualityError(failed)
    return results


def dbt_env(settings: Settings, silver_dir: Path, target: str = "local") -> dict[str, str]:
    return {
        "AZM365_SILVER_DIR": silver_dir.as_posix(),
        "AZM365_DUCKDB_PATH": settings.warehouse_path.as_posix(),
        "DBT_TARGET": target,
        # Keep dbt's writable state out of the (read-only, once deployed) project directory.
        "DBT_TARGET_PATH": (settings.data_dir / "dbt_target").as_posix(),
        "DBT_LOG_PATH": (settings.data_dir / "dbt_logs").as_posix(),
    }


def run_dbt(
    settings: Settings,
    args: list[str] | None = None,
    *,
    lake: Lake | None = None,
    target: str | None = None,
) -> None:
    """Run dbt in a child process against the silver Parquet files.

    A child process (rather than ``dbtRunner`` in-process) releases the DuckDB file as soon as
    dbt exits, so :func:`publish_gold` can open the warehouse right after, and a dbt crash can
    never take the Functions host down with it.
    """
    lake = lake or open_lake(settings)
    silver_dir = silver.read_dir(settings, lake)
    silver.ensure_empty_tables(silver_dir)
    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    env = dbt_env(settings, silver_dir, target or os.environ.get("DBT_TARGET", "local"))
    project = settings.dbt_project_dir
    cli_args = [
        *(args or ["build"]),
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--target",
        env["DBT_TARGET"],
    ]
    log.info("dbt %s", " ".join(cli_args))
    proc = subprocess.run(  # noqa: S603 - fixed interpreter and module, args from our CLI
        [sys.executable, "-m", "dbt.cli.main", *cli_args],
        env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    if proc.returncode != 0:
        raise TransformError(f"dbt {' '.join(args or ['build'])} failed (exit {proc.returncode})")


def mart_names(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts' ORDER BY 1"
        ).fetchall()
    ]


def publish_gold(settings: Settings, *, lake: Lake | None = None) -> dict[str, int]:
    """Copy every dbt mart to the gold container as Parquet (``gold/<mart>/<mart>.parquet``)."""
    lake = lake or open_lake(settings)
    out: dict[str, int] = {}
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        for name in mart_names(con):
            tbl = con.sql(f'SELECT * FROM marts."{name}"').to_arrow_table()
            buf = io.BytesIO()
            pq.write_table(tbl, buf, compression="zstd")
            lake.put("gold", f"{name}/{name}.parquet", buf.getvalue())
            out[name] = tbl.num_rows
    finally:
        con.close()
    lake.put(
        "ops",
        "runs/latest_gold.json",
        json.dumps(
            {"published_at": datetime.now(UTC).isoformat(), "marts": out}, indent=1
        ).encode(),
    )
    log.info("gold: published %d marts", len(out))
    return out


def run_all(
    settings: Settings,
    *,
    sources: list[str] | None = None,
    as_of: str | None = None,
    fixtures: Path | None = None,
    force: bool = False,
    max_pages: int | None = None,
    skip_extract: bool = False,
) -> dict[str, Any]:
    lake = open_lake(settings)
    summary: dict[str, Any] = {}
    if not skip_extract:
        landed = extract(
            settings,
            sources,
            as_of=as_of,
            fixtures=fixtures,
            force=force,
            max_pages=max_pages,
            lake=lake,
        )
        summary["landed"] = len(landed)
    summary["silver"] = build_silver(settings, lake=lake)
    results = run_dq(settings, lake=lake)
    summary["dq"] = {"checks": len(results), "failed_warn": sum(not r.passed for r in results)}
    run_dbt(settings, lake=lake)
    summary["gold"] = publish_gold(settings, lake=lake)
    return summary
