from __future__ import annotations

import json
from datetime import UTC, datetime

from azm365 import bronze
from azm365.extract import ExtractContext, github, prices, wayback
from azm365.http import Response
from azm365.lake import FileLake


class ScriptedFetcher:
    """Answers GETs from (predicate, status, body) rules, first match wins; records calls."""

    def __init__(self, rules):
        self.rules = rules
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None, headers=None, *, accept_status=(200,)):
        self.calls.append((url, params))
        for match, status, body in self.rules:
            if match(url, params or {}):
                value = body(url, params or {}) if callable(body) else body
                data = json.dumps(value).encode() if value is not None else b""
                return Response(status, data, {})
        raise AssertionError(f"unexpected GET {url} {params}")


def make_ctx(tmp_path, fetcher, config, **kw):
    return ExtractContext(
        lake=FileLake(tmp_path),
        fetcher=fetcher,
        config=config,
        as_of=datetime(2026, 1, 5, 12, tzinfo=UTC),
        sleep=lambda _s: None,
        **kw,
    )


PRICES_CFG = {
    "azure_prices": {
        "endpoint": "https://prices.test/api/retail/prices",
        "api_version": "2023-01-01-preview",
        "scopes": [{"name": "s", "filters": ["serviceName eq 'X'"]}],
    }
}
NEXT = "https://prices.test:443/api/retail/prices?$skip="


def _page(n, nxt):
    return {"Items": [{"meterId": f"m{n}"}], "NextPageLink": nxt}


def test_prices_follow_next_page_links_until_done(tmp_path):
    f = ScriptedFetcher(
        [
            (lambda u, p: "$skip=2" in u, 200, _page(3, None)),
            (lambda u, p: "$skip=1" in u, 200, _page(2, NEXT + "2")),
            (lambda u, p: p.get("$filter") == "serviceName eq 'X'", 200, _page(1, NEXT + "1")),
        ]
    )
    ctx = make_ctx(tmp_path, f, PRICES_CFG)
    [rec] = prices.extract(ctx)
    assert rec["item_count"] == 3
    assert rec["complete"]
    assert f.calls[0][1] == {"$filter": "serviceName eq 'X'", "api-version": "2023-01-01-preview"}
    # Same day again: skipped without a single request.
    f.calls.clear()
    assert prices.extract(ctx) == []
    assert f.calls == []


def test_prices_max_pages_marks_snapshot_incomplete(tmp_path):
    f = ScriptedFetcher([(lambda u, p: True, 200, _page(1, NEXT + "1"))])
    [rec] = prices.extract(make_ctx(tmp_path, f, PRICES_CFG, max_pages=1))
    assert rec["item_count"] == 1
    assert not rec["complete"]
    assert len(f.calls) == 1


GH_CFG = {
    "github": {"api": "https://gh.test", "repos": ["o/r"], "pull_pages": 2, "release_pages": 1}
}


def test_github_waits_for_participation_stats_and_minimises_payload(tmp_path):
    attempts = {"n": 0}

    def stats(_u, _p):
        attempts["n"] += 1
        return None if attempts["n"] < 2 else {"all": [1] * 52}

    pull = {
        "number": 1,
        "state": "closed",
        "created_at": "2026-01-01T00:00:00Z",
        "merged_at": None,
        "user": {"login": "renovate[bot]", "type": "Bot", "email": "x@example.com"},
        "body": "free text that must not be stored",
    }
    f = ScriptedFetcher(
        [
            (lambda u, p: u.endswith("/stats/participation") and attempts["n"] < 1, 202, stats),
            (lambda u, p: u.endswith("/stats/participation"), 200, stats),
            (lambda u, p: u.endswith("/releases"), 200, []),
            (lambda u, p: u.endswith("/pulls"), 200, [pull]),
            (
                lambda u, p: u == "https://gh.test/repos/o/r",
                200,
                {"full_name": "o/r", "license": {"spdx_id": "MIT"}},
            ),
        ]
    )
    [rec] = github.extract(make_ctx(tmp_path, f, GH_CFG))
    env = bronze.read_snapshot(FileLake(tmp_path), rec)
    assert env["payload"]["weekly_commits"] == [1] * 52
    assert rec["complete"]
    [p] = env["payload"]["pulls"]
    assert p["author_is_bot"]
    assert "body" not in p
    assert "user" not in p
    assert env["payload"]["repo"]["license"] == "MIT"


def test_github_records_null_when_stats_never_ready(tmp_path):
    f = ScriptedFetcher(
        [
            (lambda u, p: u.endswith("/stats/participation"), 202, None),
            (lambda u, p: u.endswith(("/releases", "/pulls")), 200, []),
            (lambda u, p: True, 200, {"full_name": "o/r"}),
        ]
    )
    [rec] = github.extract(make_ctx(tmp_path, f, GH_CFG))
    assert not rec["complete"]


WB_CFG = {
    "wayback": {
        "cdx": "https://wb.test/cdx",
        "replay": "https://wb.test/web",
        "targets": [
            {
                "source": "m365_roadmap",
                "dataset": "features",
                "scope": "all",
                "url": "https://rm.test/api",
                "from": "2025",
            }
        ],
    }
}


def test_wayback_lands_past_captures_only_once(tmp_path):
    header = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]
    rows = [
        ["k", "20251201000000", "https://rm.test/api", "", "200", "d1", "1"],
        ["k", "20270101000000", "https://rm.test/api", "", "200", "d2", "1"],
    ]
    f = ScriptedFetcher(
        [
            (lambda u, p: u == "https://wb.test/cdx", 200, [header, *rows]),
            (lambda u, p: "id_/" in u, 200, [{"id": 1, "title": "t", "status": "Launched"}]),
        ]
    )
    ctx = make_ctx(tmp_path, f, WB_CFG)
    landed = wayback.extract(ctx)
    # The 2027 capture is in the future relative to as_of and is ignored.
    assert [r["snapshot_id"] for r in landed] == ["20251201T000000Z-wayback"]
    assert wayback.extract(ctx) == []
