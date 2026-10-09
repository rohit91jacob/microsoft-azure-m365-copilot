"""Blob lake against the Azurite emulator (`npm run azurite`). Skipped when it is not running."""

from __future__ import annotations

import os
import socket
import uuid
from urllib.parse import urlsplit

import pytest
import requests

from azm365 import bronze, pipeline
from azm365.lake import BlobLake
from azm365.settings import Settings
from tests.conftest import DAY1, FIXTURES

pytestmark = pytest.mark.azurite

# Azurite's fixed, publicly documented development account (not a secret).
DEV_CONN = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"  # gitleaks:allow
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


def _endpoint(conn: str) -> tuple[str, int]:
    parts = dict(p.split("=", 1) for p in conn.split(";") if "=" in p)
    url = urlsplit(parts.get("BlobEndpoint", "http://127.0.0.1:10000"))
    return url.hostname or "127.0.0.1", url.port or 10000


def _azurite_up(conn: str) -> bool:
    """True only if something that speaks the Blob API answers (not just an open port)."""
    host, port = _endpoint(conn)
    try:
        with socket.create_connection((host, port), timeout=1):
            pass
        r = requests.get(f"http://{host}:{port}/devstoreaccount1?comp=list", timeout=3)
    except (OSError, requests.RequestException):
        return False
    return "x-ms-request-id" in {k.lower() for k in r.headers} or "Azurite" in r.headers.get(
        "Server", ""
    )


@pytest.fixture
def blob_settings(tmp_path):
    conn = os.environ.get("AZM365_AZURITE_CONNECTION_STRING", DEV_CONN)
    if not _azurite_up(conn):
        host, port = _endpoint(conn)
        pytest.skip(f"no Blob endpoint at {host}:{port} (start one with `npm run azurite`)")
    return Settings(data_dir=tmp_path / "data", lake="azurite", blob_connection_string=conn)


def test_blob_lake_roundtrip(blob_settings):
    from azure.storage.blob import BlobServiceClient

    lake = BlobLake(BlobServiceClient.from_connection_string(blob_settings.blob_connection_string))
    path = f"it/{uuid.uuid4()}.txt"
    assert lake.put("ops", path, b"hello")
    assert not lake.put("ops", path, b"again", overwrite=False)
    assert lake.get("ops", path) == b"hello"
    assert lake.exists("ops", path)
    assert path in lake.list("ops", "it/")


def test_pipeline_against_azurite(blob_settings):
    """Extract -> silver -> DQ -> dbt -> gold with every container on the Blob emulator."""
    from azure.storage.blob import BlobServiceClient

    svc = BlobServiceClient.from_connection_string(blob_settings.blob_connection_string)
    for c in ("bronze", "silver", "gold", "ops"):  # start from empty containers
        cc = svc.get_container_client(c)
        if cc.exists():
            for b in cc.list_blobs():
                cc.delete_blob(b.name)
    summary = pipeline.run_all(blob_settings, as_of=DAY1, fixtures=FIXTURES)
    assert summary["landed"] == 13
    lake = BlobLake(svc)
    assert len(bronze.list_manifests(lake)) == 13
    assert lake.exists("gold", "mart_price_changes/mart_price_changes.parquet")
    assert lake.exists("ops", "dq_results/latest.json")
    # Silver was mirrored locally for DuckDB/dbt, exactly as a Function instance would.
    assert any(blob_settings.silver_mirror.rglob("*.parquet"))
    again = pipeline.run_all(blob_settings, as_of=DAY1, fixtures=FIXTURES)
    assert again["landed"] == 0
    assert again["silver"]["built"] == 0
