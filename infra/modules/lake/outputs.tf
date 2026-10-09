output "account_id" {
  description = "Lake storage account ID (RBAC scope)."
  value       = azurerm_storage_account.lake.id
}

output "account_name" {
  description = "Lake storage account name."
  value       = azurerm_storage_account.lake.name
}

output "blob_endpoint" {
  description = "Blob endpoint, used by the pipeline (AZM365_STORAGE_ACCOUNT_URL)."
  value       = azurerm_storage_account.lake.primary_blob_endpoint
}

output "dfs_endpoint" {
  description = "ADLS Gen2 (dfs) endpoint, used by Data Factory."
  value       = azurerm_storage_account.lake.primary_dfs_endpoint
}
