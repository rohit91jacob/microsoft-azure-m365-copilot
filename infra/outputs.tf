output "resource_group_name" {
  description = "Resource group holding everything."
  value       = azurerm_resource_group.this.name
}

output "lake_account_name" {
  description = "ADLS Gen2 lake account."
  value       = module.lake.account_name
}

output "lake_blob_endpoint" {
  description = "AZM365_STORAGE_ACCOUNT_URL for the pipeline."
  value       = module.lake.blob_endpoint
}

output "function_app_name" {
  description = "Function App name (AZURE_FUNCTIONAPP_NAME for the deploy workflow)."
  value       = module.function_app.name
}

output "key_vault_uri" {
  description = "Key Vault URI."
  value       = module.key_vault.vault_uri
}

output "data_factory_name" {
  description = "Data Factory name."
  value       = module.data_factory.name
}

output "deploy_client_id" {
  description = "AZURE_CLIENT_ID for the deploy workflow (null when github_repository is empty)."
  value       = one(module.github_oidc[*].client_id)
}

output "tenant_id" {
  description = "AZURE_TENANT_ID for the deploy workflow."
  value       = data.azurerm_client_config.current.tenant_id
}
