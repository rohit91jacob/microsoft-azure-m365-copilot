"""Lake storage: the same four containers on a local directory, Azurite, or ADLS Gen2.

ADLS Gen2 accounts (hierarchical namespace enabled) also serve the Blob API, so one client
covers Azurite locally and the real account in Azure. Writes are whole-object: atomic on the
local backend (temp file, then rename) and single-shot uploads on Blob, so a crashed run never
leaves a half-written object behind.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Protocol

from azm365.settings import Settings

CONTAINERS = ("bronze", "silver", "gold", "ops")


class Lake(Protocol):
    kind: str

    def put(self, container: str, path: str, data: bytes, *, overwrite: bool = True) -> bool:
        """Write an object. Returns False when it already existed and ``overwrite`` is False."""
        ...

    def get(self, container: str, path: str) -> bytes: ...

    def exists(self, container: str, path: str) -> bool: ...

    def list(self, container: str, prefix: str = "") -> list[str]: ...


def check_path(container: str, path: str) -> None:
    if container not in CONTAINERS:
        raise ValueError(f"unknown container {container!r}")
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or "\\" in path:
        raise ValueError(f"unsafe object path {path!r}")


class FileLake:
    kind = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        for c in CONTAINERS:
            (root / c).mkdir(parents=True, exist_ok=True)

    def _p(self, container: str, path: str) -> Path:
        check_path(container, path)
        return self.root / container / path

    def put(self, container: str, path: str, data: bytes, *, overwrite: bool = True) -> bool:
        target = self._p(container, path)
        if target.exists() and not overwrite:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            Path(tmp).replace(target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return True

    def get(self, container: str, path: str) -> bytes:
        return self._p(container, path).read_bytes()

    def exists(self, container: str, path: str) -> bool:
        return self._p(container, path).is_file()

    def list(self, container: str, prefix: str = "") -> list[str]:
        check_path(container, prefix)
        base = self.root / container
        out = (
            p.relative_to(base).as_posix()
            for p in base.rglob("*")
            if p.is_file() and not p.name.startswith(".tmp-")
        )
        return sorted(p for p in out if p.startswith(prefix))


class BlobLake:
    """Azure Blob / ADLS Gen2 through the Blob endpoint. Missing containers are created."""

    kind = "blob"

    def __init__(self, service_client, *, create_containers: bool = True) -> None:
        from azure.core.exceptions import ResourceExistsError

        self._svc = service_client
        if create_containers:
            for c in CONTAINERS:
                with contextlib.suppress(ResourceExistsError):
                    service_client.create_container(c)

    def _blob(self, container: str, path: str):
        check_path(container, path)
        return self._svc.get_blob_client(container=container, blob=path)

    def put(self, container: str, path: str, data: bytes, *, overwrite: bool = True) -> bool:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._blob(container, path).upload_blob(data, overwrite=overwrite)
        except ResourceExistsError:
            return False
        return True

    def get(self, container: str, path: str) -> bytes:
        return self._blob(container, path).download_blob().readall()

    def exists(self, container: str, path: str) -> bool:
        return bool(self._blob(container, path).exists())

    def list(self, container: str, prefix: str = "") -> list[str]:
        check_path(container, prefix)
        cc = self._svc.get_container_client(container)
        return sorted(b.name for b in cc.list_blobs(name_starts_with=prefix or None))


def open_lake(settings: Settings) -> Lake:
    if settings.lake == "local":
        return FileLake(settings.lake_root)
    from azure.storage.blob import BlobServiceClient

    if settings.lake == "azurite":
        if not settings.blob_connection_string:
            raise ValueError("AZM365_LAKE=azurite needs AZM365_BLOB_CONNECTION_STRING")
        return BlobLake(BlobServiceClient.from_connection_string(settings.blob_connection_string))
    if not settings.storage_account_url:
        raise ValueError("AZM365_LAKE=adls needs AZM365_STORAGE_ACCOUNT_URL")
    from azure.identity import DefaultAzureCredential

    # Managed identity inside Azure, `az login` on a laptop. No account keys or SAS tokens;
    # containers are created by Terraform, so the identity only needs data-plane RBAC.
    return BlobLake(
        BlobServiceClient(settings.storage_account_url, credential=DefaultAzureCredential()),
        create_containers=False,
    )
