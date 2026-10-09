"""GitHub REST API on public Microsoft repositories.

Per repository and day: repo metadata, 52 weeks of default-branch commit counts, recent releases and the most
recently updated closed pull requests. Free text (PR and release bodies) and personal profile
fields are dropped at extraction; only whether an author is a bot is kept.
"""

from __future__ import annotations

import logging
from typing import Any

from azm365 import bronze
from azm365.extract import ExtractContext

log = logging.getLogger(__name__)

SOURCE, DATASET = "github", "repo_activity"

REPO_FIELDS = (
    "full_name",
    "created_at",
    "pushed_at",
    "stargazers_count",
    "forks_count",
    "open_issues_count",
    "subscribers_count",
    "default_branch",
    "language",
    "archived",
    "size",
)


def _headers(token: str | None) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _is_bot(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    return user.get("type") == "Bot" or str(user.get("login", "")).endswith("[bot]")


def minimise_pull(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": p["number"],
        "state": p["state"],
        "draft": bool(p.get("draft")),
        "created_at": p["created_at"],
        "closed_at": p.get("closed_at"),
        "merged_at": p.get("merged_at"),
        "updated_at": p.get("updated_at"),
        "base_ref": (p.get("base") or {}).get("ref"),
        "author_is_bot": _is_bot(p.get("user")),
        "author_association": p.get("author_association"),
    }


def minimise_release(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "tag_name": r["tag_name"],
        "name": r.get("name"),
        "draft": bool(r.get("draft")),
        "prerelease": bool(r.get("prerelease")),
        "created_at": r.get("created_at"),
        "published_at": r.get("published_at"),
        "assets": len(r.get("assets") or []),
    }


def _participation(ctx: ExtractContext, base: str, headers: dict[str, str], attempts: int = 6):
    """Weekly default-branch commit counts for the last 52 weeks (oldest first).

    GitHub computes statistics lazily and answers 202 until they are cached. The participation
    statistic is used rather than ``commit_activity`` because the latter stays at 202 for many
    minutes on very large repositories such as microsoft/vscode.
    """
    for attempt in range(attempts):
        resp = ctx.fetcher.get(
            f"{base}/stats/participation", headers=headers, accept_status=(200, 202)
        )
        if resp.status == 200 and resp.body.strip():
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("all"), list):
                return [int(n) for n in data["all"]]
        if attempt + 1 < attempts:
            ctx.sleep(5 * (attempt + 1))
    log.warning("github: participation stats for %s not ready; recording null", base)
    return None


def extract(ctx: ExtractContext) -> list[dict[str, Any]]:
    cfg = ctx.config["github"]
    headers = _headers(ctx.github_token)
    landed = []
    for repo in cfg["repos"]:
        ref = bronze.SnapshotRef(
            SOURCE, DATASET, repo.replace("/", "__"), bronze.snapshot_id(ctx.day_start, "live")
        )
        if not ctx.force and bronze.exists(ctx.lake, ref):
            log.info("github/%s: %s already landed, skipping", repo, ref.snapshot_id)
            continue
        base = f"{cfg['api']}/repos/{repo}"
        meta = ctx.fetcher.get(base, headers=headers).json()
        releases: list[dict[str, Any]] = []
        for page in range(1, int(cfg.get("release_pages", 1)) + 1):
            batch = ctx.fetcher.get(
                f"{base}/releases", {"per_page": "100", "page": str(page)}, headers
            ).json()
            releases.extend(minimise_release(r) for r in batch)
            if len(batch) < 100:
                break
        pulls: list[dict[str, Any]] = []
        for page in range(1, int(cfg.get("pull_pages", 3)) + 1):
            params = {
                "state": "closed",
                "sort": "updated",
                "direction": "desc",
                "per_page": "100",
                "page": str(page),
            }
            batch = ctx.fetcher.get(f"{base}/pulls", params, headers).json()
            pulls.extend(minimise_pull(p) for p in batch)
            if len(batch) < 100:
                break
        payload = {
            "repo": {k: meta.get(k) for k in REPO_FIELDS}
            | {"license": (meta.get("license") or {}).get("spdx_id")},
            "weekly_commits": _participation(ctx, base, headers),
            "releases": releases,
            "pulls": pulls,
        }
        record = bronze.write_snapshot(
            ctx.lake,
            ref,
            payload,
            item_count=len(releases) + len(pulls),
            complete=payload["weekly_commits"] is not None,
            request={"repo": repo, "release_pages": cfg.get("release_pages", 1)},
            synthetic=ctx.synthetic,
            force=ctx.force,
        )
        if record:
            landed.append(record)
    return landed
