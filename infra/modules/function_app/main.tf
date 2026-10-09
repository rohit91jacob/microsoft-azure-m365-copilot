# Azure Functions on the Flex Consumption plan (Linux, Python 3.12): the four timer-triggered
# extractors and the HTTP "transform" endpoint (functions/function_app.py). A system-assigned
# managed identity reaches the lake, the host storage and Key Vault through RBAC; no
# connection strings or account keys exist anywhere.

resource "azurerm_storage_account" "host" {
  #checkov:skip=CKV_AZURE_59:Public endpoint stays enabled only so service endpoints work; network_rules default to Deny (Function subnet + trusted services only).
  #checkov:skip=CKV2_AZURE_33:Service endpoints instead of private endpoints (~USD 7/month each, plus private DNS); see README cost notes.
  #checkov:skip=CKV_AZURE_33:Queue logging is set by azurerm_storage_account_queue_properties.host (azurerm 5.x moved it out of this resource).
  #checkov:skip=CKV_AZURE_206:Host storage holds only the deployment package and transient host state (leases, timer schedules); ZRS is enough and it is rebuilt by a redeploy.
  #checkov:skip=CKV2_AZURE_1:No pipeline data is stored here (code package and host state only); Microsoft-managed keys with infrastructure encryption.
  name                     = var.host_storage_account_name
  location                 = var.location
  resource_group_name      = var.resource_group_name
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "ZRS"

  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  cross_tenant_replication_enabled  = false
  local_user_enabled                = false
  infrastructure_encryption_enabled = true
  allowed_copy_scope                = "AAD"
  public_network_access             = "Enabled" # gated by network_rules below
  tags                              = var.tags

  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices", "Logging", "Metrics"]
    virtual_network_subnet_ids = [var.subnet_id]
    ip_rules                   = var.allowed_ip_ranges
  }

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  sas_policy {
    expiration_period = "00.01:00:00"
    expiration_action = "Block"
  }
}

resource "azurerm_storage_account_queue_properties" "host" {
  storage_account_id = azurerm_storage_account.host.id

  logging {
    delete                = true
    read                  = true
    write                 = true
    version               = "1.0"
    retention_policy_days = 7
  }
}

resource "azurerm_storage_container" "deployments" {
  #checkov:skip=CKV2_AZURE_21:Blob read/write/delete logging is enabled on the account's blob service (azurerm_monitor_diagnostic_setting.host_blob).
  name                  = "deployments"
  storage_account_id    = azurerm_storage_account.host.id
  container_access_type = "private"
}

resource "azurerm_service_plan" "this" {
  #checkov:skip=CKV_AZURE_212:Flex Consumption (FC1) has no fixed worker count; the platform scales per invocation and the jobs are idempotent daily batches.
  #checkov:skip=CKV_AZURE_225:Zone redundancy is not a plan-level setting for Flex Consumption; a failed daily run is retried by the next timer/ADF retry.
  name                = "asp-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = var.tags
}

resource "azurerm_function_app_flex_consumption" "this" {
  name                = "func-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  service_plan_id     = azurerm_service_plan.this.id

  runtime_name    = "python"
  runtime_version = "3.12"

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.host.primary_blob_endpoint}${azurerm_storage_container.deployments.name}"
  storage_authentication_type = "SystemAssignedIdentity"

  # Cost guardrails: a low scale-out ceiling; dbt + DuckDB fit in 2 GB instances.
  maximum_instance_count = var.maximum_instance_count
  instance_memory_in_mb  = 2048

  https_only                                     = true
  public_network_access_enabled                  = true # ip_restriction below admits Data Factory only
  client_certificate_enabled                     = false
  webdeploy_publish_basic_authentication_enabled = false
  virtual_network_subnet_id                      = var.subnet_id
  tags                                           = var.tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    minimum_tls_version                    = "1.2"
    scm_minimum_tls_version                = "1.2"
    http2_enabled                          = true
    remote_debugging_enabled               = false
    application_insights_connection_string = var.application_insights_connection_string
    ip_restriction_default_action          = "Deny"
    # Code deploys authenticate with Entra ID (GitHub OIDC); basic auth publishing is off.
    scm_ip_restriction_default_action = "Allow"

    ip_restriction {
      name        = "allow-data-factory"
      action      = "Allow"
      priority    = 100
      service_tag = "DataFactory"
    }
  }

  app_settings = merge(
    {
      # Host storage through the managed identity (no AzureWebJobsStorage connection string).
      "AzureWebJobsStorage__accountName"          = azurerm_storage_account.host.name
      "APPLICATIONINSIGHTS_AUTHENTICATION_STRING" = "Authorization=AAD"
      "AZM365_LAKE"                               = "adls"
      "AZM365_STORAGE_ACCOUNT_URL"                = var.lake_blob_endpoint
      "AZM365_DATA_DIR"                           = "/tmp/azm365"
      "AZM365_CONFIG"                             = "/home/site/wwwroot/config/sources.yml"
      "AZM365_DBT_PROJECT_DIR"                    = "/home/site/wwwroot/dbt"
      "AZM365_LOG_FORMAT"                         = "json"
      "DBT_TARGET"                                = "local"
    },
    var.github_token_secret_uri == "" ? {} : {
      # Key Vault reference, resolved by the platform with the app identity.
      "GITHUB_TOKEN" = "@Microsoft.KeyVault(SecretUri=${var.github_token_secret_uri})"
    },
  )
}

locals {
  principal_id = azurerm_function_app_flex_consumption.this.identity[0].principal_id
  # Data-plane roles the Functions host needs on its own storage (blob leases, timer
  # schedules in queues/tables), plus the lake, Key Vault and telemetry roles of the pipeline.
  role_assignments = {
    host_blob  = { scope = azurerm_storage_account.host.id, role = "Storage Blob Data Owner" }
    host_queue = { scope = azurerm_storage_account.host.id, role = "Storage Queue Data Contributor" }
    host_table = { scope = azurerm_storage_account.host.id, role = "Storage Table Data Contributor" }
    lake_blob  = { scope = var.lake_account_id, role = "Storage Blob Data Contributor" }
    kv_secrets = { scope = var.key_vault_id, role = "Key Vault Secrets User" }
    telemetry  = { scope = var.application_insights_id, role = "Monitoring Metrics Publisher" }
  }
}

resource "azurerm_role_assignment" "app" {
  for_each             = local.role_assignments
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = local.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_monitor_diagnostic_setting" "host_blob" {
  name                       = "to-log-analytics"
  target_resource_id         = "${azurerm_storage_account.host.id}/blobServices/default"
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "StorageRead"
  }

  enabled_log {
    category = "StorageWrite"
  }

  enabled_log {
    category = "StorageDelete"
  }

  enabled_metric {
    category = "Transaction"
  }
}

resource "azurerm_monitor_diagnostic_setting" "app" {
  name                       = "to-log-analytics"
  target_resource_id         = azurerm_function_app_flex_consumption.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category = "FunctionAppLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# Alert when any extractor or the transform fails.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "failures" {
  name                 = "alert-${var.name}-function-failures"
  location             = var.location
  resource_group_name  = var.resource_group_name
  scopes               = [var.application_insights_id]
  severity             = 2
  evaluation_frequency = "PT1H"
  window_duration      = "PT1H"
  description          = "An azm365 function invocation failed in the last hour."
  tags                 = var.tags

  criteria {
    query                   = "requests | where success == false"
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "GreaterThan"
  }

  action {
    action_groups = [var.action_group_id]
  }
}
