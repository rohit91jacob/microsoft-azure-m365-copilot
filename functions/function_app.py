"""Azure Functions app (Python v2 programming model).

* Four timer-triggered extractors land raw snapshots in the ADLS Gen2 ``bronze`` container.
  Each is idempotent per UTC day, so a retried or duplicated timer invocation is a no-op.
* ``transform`` (HTTP, function-key auth) runs bronze -> silver -> DQ gate -> dbt build ->
  publish gold. The Azure Data Factory pipeline in ``orchestration/adf`` calls it once the
  extract window has passed and retries it on failure.

All configuration comes from app settings (see ``infra/modules/function_app``); storage is
reached with the app's managed identity, never with keys. Locally the same app runs against
Azurite with ``func start`` (see docs/runbook.md).
"""

from __future__ import annotations

import json
import logging

import azure.functions as func

from azm365 import pipeline
from azm365.settings import Settings

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
log = logging.getLogger("azm365.functions")

# NCRONTAB (seconds first), UTC. Prices and the roadmap change daily; GitHub stats are daily.
SCHEDULES = {
    "azure_prices": "0 15 2 * * *",
    "m365_roadmap": "0 20 2 * * *",
    "github": "0 25 2 * * *",
    # Internet Archive history only grows slowly; once a week is plenty.
    "wayback": "0 0 3 * * 0",
}


def run_extract(source: str) -> dict[str, int]:
    landed = pipeline.extract(Settings.from_env(), [source])
    result = {"landed": len(landed), "items": sum(r["item_count"] for r in landed)}
    log.info("extract %s: %s", source, json.dumps(result))
    return result


@app.function_name("extract_azure_prices")
@app.timer_trigger(arg_name="timer", schedule=SCHEDULES["azure_prices"], run_on_startup=False)
def extract_azure_prices(timer: func.TimerRequest) -> None:
    if timer.past_due:
        log.warning("extract_azure_prices is running late")
    run_extract("azure_prices")


@app.function_name("extract_m365_roadmap")
@app.timer_trigger(arg_name="timer", schedule=SCHEDULES["m365_roadmap"], run_on_startup=False)
def extract_m365_roadmap(timer: func.TimerRequest) -> None:
    run_extract("m365_roadmap")


@app.function_name("extract_github")
@app.timer_trigger(arg_name="timer", schedule=SCHEDULES["github"], run_on_startup=False)
def extract_github(timer: func.TimerRequest) -> None:
    run_extract("github")


@app.function_name("extract_wayback")
@app.timer_trigger(arg_name="timer", schedule=SCHEDULES["wayback"], run_on_startup=False)
def extract_wayback(timer: func.TimerRequest) -> None:
    run_extract("wayback")


@app.function_name("transform")
@app.route(route="transform", methods=["POST"])
def transform(req: func.HttpRequest) -> func.HttpResponse:
    """bronze -> silver -> DQ -> dbt -> gold. 200 ok, 422 DQ failure, 500 dbt failure."""
    settings = Settings.from_env()
    try:
        summary = pipeline.run_all(settings, skip_extract=True)
    except pipeline.DataQualityError as exc:
        log.error("transform: %s", exc)
        body = {"status": "dq_failed", "failed_checks": [f.name for f in exc.failed]}
        return func.HttpResponse(json.dumps(body), status_code=422, mimetype="application/json")
    except pipeline.TransformError as exc:
        log.error("transform: %s", exc)
        return func.HttpResponse(
            json.dumps({"status": "dbt_failed", "error": str(exc)}),
            status_code=500,
            mimetype="application/json",
        )
    return func.HttpResponse(
        json.dumps({"status": "ok", **summary}, default=str), mimetype="application/json"
    )
