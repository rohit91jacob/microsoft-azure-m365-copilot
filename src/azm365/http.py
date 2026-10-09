"""HTTP access for the extractors: a live fetcher with retries, and an offline fixture fetcher.

Both implement ``get(url, params, headers) -> Response`` so every extractor runs unchanged
against the real APIs or against the committed (synthetic) fixtures used in tests and CI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8-sig"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8-sig")


class FetchError(RuntimeError):
    pass


class Fetcher(Protocol):
    def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        *,
        accept_status: tuple[int, ...] = (200,),
    ) -> Response: ...


def canonical_url(url: str, params: dict[str, str] | None = None) -> str:
    """Normalise a request so the same logical request always maps to the same key.

    Drops default ports (the Retail Prices API returns ``prices.azure.com:443`` in
    ``NextPageLink``) and appends params in sorted order.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = parts.query
    if params:
        extra = urlencode(sorted(params.items()))
        query = f"{query}&{extra}" if query else extra
    return urlunsplit((parts.scheme.lower(), host, parts.path, query, ""))


def request_key(url: str, params: dict[str, str] | None = None) -> str:
    return hashlib.sha1(canonical_url(url, params).encode(), usedforsecurity=False).hexdigest()[:20]


class LiveFetcher:
    """``requests`` with exponential backoff on 429/5xx and connection errors."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 60.0,
        retries: int = 5,
        backoff: float = 2.0,
        sleep=time.sleep,
    ) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep

    def get(self, url, params=None, headers=None, *, accept_status=(200,)) -> Response:
        last: str = ""
        for attempt in range(self._retries + 1):
            try:
                r = self._session.get(url, params=params, headers=headers, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code in accept_status:
                    return Response(r.status_code, r.content, dict(r.headers))
                last = f"HTTP {r.status_code}"
                if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
                    reset = r.headers.get("X-RateLimit-Reset", "?")
                    raise FetchError(
                        f"GET {canonical_url(url, params)} -> {last}: rate limit exhausted "
                        f"(resets at epoch {reset}); set GITHUB_TOKEN to raise the limit"
                    )
                if r.status_code not in RETRY_STATUS:
                    raise FetchError(f"GET {canonical_url(url, params)} -> {last}")
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    self._sleep(min(int(retry_after), 120))
                    continue
            if attempt < self._retries:
                delay = self._backoff * (2**attempt)
                log.warning("GET %s failed (%s); retry in %.0fs", url, last, delay)
                self._sleep(delay)
        raise FetchError(f"GET {canonical_url(url, params)} failed after retries: {last}")


class FixtureFetcher:
    """Serves responses from ``<root>/<as_of>/<key>.*`` then ``<root>/common/<key>.*``.

    Fixture files are produced by ``scripts/make_fixtures.py`` with the same
    :func:`request_key`, so an unknown request fails loudly instead of silently returning data.
    """

    def __init__(self, root: Path, as_of: str | None = None) -> None:
        self.root = root
        self.as_of = as_of

    def _find(self, key: str) -> Path | None:
        dirs = [self.root / self.as_of] if self.as_of else []
        dirs.append(self.root / "common")
        for d in dirs:
            for suffix in (".json", ".txt", ".xml"):
                p = d / f"{key}{suffix}"
                if p.is_file():
                    return p
        return None

    def get(self, url, params=None, headers=None, *, accept_status=(200,)) -> Response:
        key = request_key(url, params)
        path = self._find(key)
        if path is None:
            raise FetchError(f"no fixture for {canonical_url(url, params)} (key {key})")
        return Response(200, path.read_bytes(), {})
