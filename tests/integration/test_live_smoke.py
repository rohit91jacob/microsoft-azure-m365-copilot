"""Opt-in live smoke test against the real public APIs: ``pytest -m network``.

Checks that each endpoint still answers with the shape the extractors expect. Credential-free
(GITHUB_TOKEN is used if set). Not part of the default run or of CI's offline jobs.
"""

from __future__ import annotations

import os

import pytest

from azm365.http import LiveFetcher
from azm365.settings import REPO_ROOT, Settings

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def fetcher():
    # The roadmap host answers 403 to some bare user agents; use the pipeline's own.
    return LiveFetcher(user_agent=Settings(data_dir=REPO_ROOT).user_agent, timeout=60, retries=2)


def test_retail_prices_first_page(fetcher):
    body = fetcher.get(
        "https://prices.azure.com/api/retail/prices",
        {
            "$filter": "serviceName eq 'Functions' and armRegionName eq 'eastus'",
            "api-version": "2023-01-01-preview",
        },
    ).json()
    assert body["BillingCurrency"] == "USD"
    assert body["Items"]
    assert {"meterId", "retailPrice", "armRegionName", "type", "effectiveStartDate"} <= set(
        body["Items"][0]
    )


def test_m365_roadmap_feed(fetcher):
    items = fetcher.get("https://www.microsoft.com/releasecommunications/api/v1/m365").json()
    assert isinstance(items, list)
    assert len(items) > 500
    assert {"id", "title", "status", "publicDisclosureAvailabilityDate", "tagsContainer"} <= set(
        items[0]
    )


def test_github_repo(fetcher):
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"} | (
        {"Authorization": f"Bearer {token}"} if token else {}
    )
    repo = fetcher.get("https://api.github.com/repos/microsoft/vscode", headers=headers).json()
    assert repo["full_name"] == "microsoft/vscode"
