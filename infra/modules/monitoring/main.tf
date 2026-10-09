# Log Analytics + workspace-based Application Insights with hard daily ingestion caps, and an
# action group that budget and failure alerts notify.

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_days
  # Cost guardrail: ingestion stops for the day once the cap is reached.
  daily_quota_gb               = var.daily_quota_gb
  local_authentication_enabled = false
  tags                         = var.tags
}

resource "azurerm_application_insights" "this" {
  name                         = "appi-${var.name}"
  location                     = var.location
  resource_group_name          = var.resource_group_name
  workspace_id                 = azurerm_log_analytics_workspace.this.id
  application_type             = "other"
  daily_data_cap_in_gb         = var.daily_quota_gb
  sampling_percentage          = 100
  local_authentication_enabled = false
  internet_ingestion_enabled   = true
  internet_query_enabled       = true
  tags                         = var.tags
}

resource "azurerm_monitor_action_group" "this" {
  name                = "ag-${var.name}"
  resource_group_name = var.resource_group_name
  short_name          = substr(replace(var.name, "-", ""), 0, 12)
  tags                = var.tags

  dynamic "email_receiver" {
    for_each = toset(var.alert_emails)
    content {
      name                    = "email-${index(var.alert_emails, email_receiver.value)}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}
