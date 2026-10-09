output "id" {
  description = "Function App resource ID."
  value       = azurerm_function_app_flex_consumption.this.id
}

output "name" {
  description = "Function App name."
  value       = azurerm_function_app_flex_consumption.this.name
}

output "default_hostname" {
  description = "Function App hostname, called by Data Factory."
  value       = azurerm_function_app_flex_consumption.this.default_hostname
}

output "principal_id" {
  description = "Object ID of the app managed identity."
  value       = local.principal_id
}
