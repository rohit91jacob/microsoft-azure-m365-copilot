"""The Azure Functions app (v2 model) registers the expected triggers and maps outcomes to HTTP."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import azure.functions as func
import pytest

from azm365 import pipeline
from azm365.quality import CheckResult
from azm365.settings import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "functions"))
import function_app

FUNCS = {f.get_function_name(): f for f in function_app.app.get_functions()}


def test_triggers_and_schedules():
    assert set(FUNCS) == {
        "extract_azure_prices",
        "extract_m365_roadmap",
        "extract_github",
        "extract_wayback",
        "transform",
    }
    timers = {
        name: next(b.schedule for b in f.get_bindings() if b.type == "timerTrigger")
        for name, f in FUNCS.items()
        if name.startswith("extract_")
    }
    assert timers["extract_azure_prices"] == "0 15 2 * * *"
    assert timers["extract_wayback"].endswith("* * 0")  # weekly
    http = next(b for b in FUNCS["transform"].get_bindings() if b.type == "httpTrigger")
    assert http.route == "transform"
    assert http.methods == [func.HttpMethod.POST] or [str(m) for m in http.methods] == ["POST"]


def _call_transform():
    req = func.HttpRequest(method="POST", url="/api/transform", body=b"{}")
    return FUNCS["transform"].get_user_function()(req)


def test_transform_maps_results_to_status_codes(monkeypatch):
    monkeypatch.setattr(pipeline, "run_all", lambda settings, **kw: {"gold": {"m": 1}})
    ok = _call_transform()
    assert ok.status_code == 200
    assert json.loads(ok.get_body())["status"] == "ok"

    def dq_fail(settings, **kw):
        raise pipeline.DataQualityError([CheckResult("c1", "prices", "error", "d", 1, False, [])])

    monkeypatch.setattr(pipeline, "run_all", dq_fail)
    bad = _call_transform()
    assert bad.status_code == 422
    assert json.loads(bad.get_body())["failed_checks"] == ["c1"]

    def dbt_fail(settings, **kw):
        raise pipeline.TransformError("boom")

    monkeypatch.setattr(pipeline, "run_all", dbt_fail)
    assert _call_transform().status_code == 500


@pytest.mark.parametrize("name", ["extract_azure_prices", "extract_github"])
def test_timer_functions_run_one_source(monkeypatch, name):
    seen = []
    monkeypatch.setattr(pipeline, "extract", lambda settings, sources: seen.append(sources) or [])
    FUNCS[name].get_user_function()(SimpleNamespace(past_due=True))
    assert seen == [[name.removeprefix("extract_")]]
