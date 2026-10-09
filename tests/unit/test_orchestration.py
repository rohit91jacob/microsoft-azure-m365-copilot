"""The ADF pipeline/trigger JSON that Terraform deploys is well-formed and wired correctly."""

from __future__ import annotations

import json

from azm365.settings import REPO_ROOT

ADF = REPO_ROOT / "orchestration" / "adf"


def _walk(activities):
    for a in activities:
        yield a
        tp = a.get("typeProperties", {})
        for key in ("ifTrueActivities", "ifFalseActivities", "activities"):
            yield from _walk(tp.get(key, []))


def test_pipeline_activities_and_dependencies():
    pl = json.loads((ADF / "pl_azm365_daily.json").read_text(encoding="utf-8"))
    acts = list(_walk(pl["properties"]["activities"]))
    names = {a["name"] for a in acts}
    assert {"CheckBronzeManifests", "IfManifestsPresent", "Transform", "FailNoBronze"} <= names
    top = {a["name"] for a in pl["properties"]["activities"]}
    for a in pl["properties"]["activities"]:
        for dep in a.get("dependsOn", []):
            assert dep["activity"] in top
    transform = next(a for a in acts if a["name"] == "Transform")
    assert transform["type"] == "AzureFunctionActivity"
    assert transform["typeProperties"]["functionName"] == "transform"
    assert transform["typeProperties"]["method"] == "POST"
    assert transform["policy"]["retry"] >= 1


def test_trigger_runs_daily_after_the_extract_timers():
    trg = json.loads((ADF / "trg_azm365_daily.json").read_text(encoding="utf-8"))
    rec = trg["properties"]["typeProperties"]["recurrence"]
    assert rec["frequency"] == "Day" and rec["interval"] == 1
    assert rec["startTime"].endswith("03:30:00Z")  # extract timers fire 02:15-02:25 UTC
    assert (
        trg["properties"]["pipelines"][0]["pipelineReference"]["referenceName"] == "pl_azm365_daily"
    )
