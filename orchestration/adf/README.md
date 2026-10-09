# Azure Data Factory orchestration

`pl_azm365_daily.json` and `trg_azm365_daily.json` are ADF resource definitions in the format
ADF Studio exports. Terraform (`infra/modules/data_factory`) reads the pipeline's `activities`
and the trigger's schedule from these files, so they are the single source of truth.

The pipeline is deliberately thin: extraction stays in the timer-triggered functions (each
idempotent per UTC day), and ADF only gates and runs the transform:

1. `CheckBronzeManifests` - Get Metadata on `bronze/_manifest` through the ADLS Gen2 linked
   service (managed identity).
2. `IfManifestsPresent` - fail fast with `AZM365_NO_BRONZE` when nothing landed.
3. `Transform` - Azure Function activity, `POST /api/transform` with the function key that ADF
   reads from Key Vault. Non-2xx responses fail the activity (422 = DQ gate, 500 = dbt).

Locally, `azm365 run --skip-extract` executes the same transform. The JSON is validated by
`tests/unit/test_orchestration.py`; it has not been deployed to a real data factory.
