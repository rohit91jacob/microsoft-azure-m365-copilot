output "log_analytics_workspace_id" {
  description = "Log Analytics workspace for diagnostic settings."
  value       = azurerm_log_analytics_workspace.this.id
}

output "application_insights_id" {
  description = "Application Insights resource ID."
  value       = azurerm_application_insights.this.id
}

output "application_insights_connection_string" {
  description = "Application Insights connection string (Entra ID auth is enforced, so this is not a secret)."
  value       = azurerm_application_insights.this.connection_string
  sensitive   = true
}

output "action_group_id" {
  description = "Action group notified by budget and failure alerts."
  value       = azurerm_monitor_action_group.this.id
}
