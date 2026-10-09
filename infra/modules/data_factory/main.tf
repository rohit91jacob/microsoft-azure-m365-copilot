# Azure Data Factory orchestrating the daily transform. The pipeline activities and the
# schedule are read from orchestration/adf/*.json (exported ADF format), so the JSON in the
# repository is the single source of truth. ADF authenticates to the lake and Key Vault with
# its managed identity and calls the Function App with a host key it reads from Key Vault.

locals {
  pipeline = jsondecode(file("${var.orchestration_dir}/pl_azm365_daily.json"))
  trigger  = jsondecode(file("${var.orchestration_dir}/trg_azm365_daily.json"))
  schedule = local.trigger.properties.typeProperties.recurrence
}

resource "azurerm_data_factory" "this" {
  #checkov:skip=CKV_AZURE_103:Pipelines are authored as JSON in this Git repository and deployed by Terraform; ADF Git integration would create a second source of truth.
  name                = "adf-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  # Authoring happens in this repository, not in ADF Studio; the public endpoint is closed.
  public_network_enabled = false
  tags                   = var.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "lake_reader" {
  scope                = var.lake_account_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_data_factory.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "kv_secrets" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_data_factory.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# The Function host key, written to Key Vault for the ADF linked service. Write-only: the
# value is sent to Key Vault but never stored in the Terraform state for this resource.
data "azurerm_function_app_host_keys" "this" {
  name                = var.function_app_name
  resource_group_name = var.resource_group_name
}

resource "azurerm_key_vault_secret" "function_key" {
  name             = "azm365-function-host-key"
  key_vault_id     = var.key_vault_id
  value_wo         = data.azurerm_function_app_host_keys.this.default_function_key
  value_wo_version = var.function_key_version
  content_type     = "Azure Functions host key"
  expiration_date  = var.secret_expiration_date
  tags             = var.tags
}

resource "azurerm_data_factory_linked_service_key_vault" "this" {
  name            = "ls_key_vault"
  data_factory_id = azurerm_data_factory.this.id
  key_vault_id    = var.key_vault_id
}

resource "azurerm_data_factory_linked_service_data_lake_storage_gen2" "lake" {
  name                 = "ls_lake"
  data_factory_id      = azurerm_data_factory.this.id
  url                  = var.lake_dfs_endpoint
  use_managed_identity = true
}

resource "azurerm_data_factory_linked_service_azure_function" "functions" {
  name            = "ls_azm365_functions"
  data_factory_id = azurerm_data_factory.this.id
  url             = "https://${var.function_app_hostname}"

  key_vault_key {
    linked_service_name = azurerm_data_factory_linked_service_key_vault.this.name
    secret_name         = azurerm_key_vault_secret.function_key.name
  }
}

resource "azurerm_data_factory_custom_dataset" "bronze_manifest" {
  name            = "ds_bronze_manifest"
  data_factory_id = azurerm_data_factory.this.id
  type            = "Binary"

  linked_service {
    name = azurerm_data_factory_linked_service_data_lake_storage_gen2.lake.name
  }

  type_properties_json = jsonencode({
    location = {
      type       = "AzureBlobFSLocation"
      fileSystem = "bronze"
      folderPath = "_manifest"
    }
  })
}

resource "azurerm_data_factory_pipeline" "daily" {
  name            = local.pipeline.name
  data_factory_id = azurerm_data_factory.this.id
  description     = local.pipeline.properties.description
  concurrency     = local.pipeline.properties.concurrency
  annotations     = local.pipeline.properties.annotations
  parameters      = { for k, v in local.pipeline.properties.parameters : k => v.defaultValue }
  activities_json = jsonencode(local.pipeline.properties.activities)

  depends_on = [
    azurerm_data_factory_custom_dataset.bronze_manifest,
    azurerm_data_factory_linked_service_azure_function.functions,
  ]
}

resource "azurerm_data_factory_trigger_schedule" "daily" {
  name            = local.trigger.name
  data_factory_id = azurerm_data_factory.this.id
  description     = local.trigger.properties.description
  frequency       = local.schedule.frequency
  interval        = local.schedule.interval
  start_time      = local.schedule.startTime
  time_zone       = local.schedule.timeZone
  activated       = var.trigger_activated

  pipeline {
    name = azurerm_data_factory_pipeline.daily.name
  }
}

resource "azurerm_monitor_diagnostic_setting" "adf" {
  name                           = "to-log-analytics"
  target_resource_id             = azurerm_data_factory.this.id
  log_analytics_workspace_id     = var.log_analytics_workspace_id
  log_analytics_destination_type = "Dedicated"

  enabled_log {
    category = "PipelineRuns"
  }

  enabled_log {
    category = "ActivityRuns"
  }

  enabled_log {
    category = "TriggerRuns"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# Alert on any failed pipeline run.
resource "azurerm_monitor_metric_alert" "failed_runs" {
  name                = "alert-${var.name}-adf-failed-runs"
  resource_group_name = var.resource_group_name
  scopes              = [azurerm_data_factory.this.id]
  description         = "A Data Factory pipeline run failed."
  severity            = 2
  frequency           = "PT1H"
  window_size         = "PT1H"
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.DataFactory/factories"
    metric_name      = "PipelineFailedRuns"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0
  }

  action {
    action_group_id = var.action_group_id
  }
}
