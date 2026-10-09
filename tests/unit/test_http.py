from __future__ import annotations

import pytest
import responses

from azm365.http import FetchError, FixtureFetcher, LiveFetcher, canonical_url, request_key


def test_canonical_url_drops_default_port_and_sorts_params():
    a = canonical_url("https://prices.azure.com:443/api/retail/prices?$skip=1000")
    assert a == "https://prices.azure.com/api/retail/prices?$skip=1000"
    assert canonical_url("https://x.test/a", {"b": "2", "a": "1"}) == "https://x.test/a?a=1&b=2"
    assert canonical_url("http://localhost:10000/x") == "http://localhost:10000/x"


def test_request_key_is_stable_and_param_order_independent():
    k1 = request_key("https://x.test/a", {"a": "1", "b": "2"})
    k2 = request_key("https://x.test/a", {"b": "2", "a": "1"})
    assert k1 == k2 and len(k1) == 20
    assert request_key("https://x.test/a") != k1


@responses.activate
def test_live_fetcher_retries_429_then_succeeds():
    url = "https://api.test/items"
    responses.add(responses.GET, url, status=429, headers={"Retry-After": "1"})
    responses.add(responses.GET, url, status=503)
    responses.add(responses.GET, url, json={"ok": True})
    sleeps: list[float] = []
    f = LiveFetcher(user_agent="t", retries=3, backoff=0.5, sleep=sleeps.append)
    assert f.get(url).json() == {"ok": True}
    assert sleeps == [1, 1.0]  # Retry-After honoured, then exponential backoff (0.5 * 2**1)


@responses.activate
def test_live_fetcher_fails_fast_on_client_error():
    responses.add(responses.GET, "https://api.test/missing", status=404)
    with pytest.raises(FetchError, match="404"):
        LiveFetcher(user_agent="t", sleep=lambda _s: None).get("https://api.test/missing")


@responses.activate
def test_live_fetcher_gives_up_after_retries():
    responses.add(responses.GET, "https://api.test/down", status=502)
    with pytest.raises(FetchError, match="after retries"):
        LiveFetcher(user_agent="t", retries=2, sleep=lambda _s: None).get("https://api.test/down")


@responses.activate
def test_live_fetcher_accepts_202_when_asked():
    responses.add(responses.GET, "https://api.test/stats", status=202, body=b"")
    r = LiveFetcher(user_agent="t").get("https://api.test/stats", accept_status=(200, 202))
    assert r.status == 202


def test_fixture_fetcher_prefers_day_dir_then_common(tmp_path):
    key = request_key("https://x.test/a")
    (tmp_path / "common").mkdir()
    (tmp_path / "2026-01-01").mkdir()
    (tmp_path / "common" / f"{key}.json").write_text('{"v": "common"}')
    assert FixtureFetcher(tmp_path, "2026-01-02").get("https://x.test/a").json() == {"v": "common"}
    (tmp_path / "2026-01-01" / f"{key}.json").write_text('{"v": "day"}')
    assert FixtureFetcher(tmp_path, "2026-01-01").get("https://x.test/a").json() == {"v": "day"}
    with pytest.raises(FetchError, match="no fixture"):
        FixtureFetcher(tmp_path, "2026-01-01").get("https://x.test/unknown")
