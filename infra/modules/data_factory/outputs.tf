output "id" {
  description = "Data Factory resource ID."
  value       = azurerm_data_factory.this.id
}

output "name" {
  description = "Data Factory name."
  value       = azurerm_data_factory.this.name
}

output "pipeline_name" {
  description = "Daily pipeline name."
  value       = azurerm_data_factory_pipeline.daily.name
}
