output "id" {
  description = "Key Vault resource ID."
  value       = azurerm_key_vault.this.id
}

output "vault_uri" {
  description = "Key Vault URI."
  value       = azurerm_key_vault.this.vault_uri
}

output "storage_cmk_versionless_id" {
  description = "Versionless ID of the lake CMK, so automatic rotation is picked up by Storage."
  value       = azurerm_key_vault_key.storage_cmk.versionless_id
}

output "storage_cmk_resource_id" {
  description = "ARM resource ID of the lake CMK (scope for the key-level role assignment)."
  value       = azurerm_key_vault_key.storage_cmk.resource_versionless_id
}
