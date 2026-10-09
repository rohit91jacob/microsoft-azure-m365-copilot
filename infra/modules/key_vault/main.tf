# Key Vault (RBAC mode, purge protection) holding the storage customer-managed key, the
# Function App host key that Data Factory uses, and the optional GitHub token.

resource "azurerm_key_vault" "this" {
  #checkov:skip=CKV2_AZURE_32:Service endpoint + firewall (default Deny) instead of a private endpoint; see README cost notes.
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  tenant_id                     = var.tenant_id
  sku_name                      = "premium" # HSM-backed keys; no base fee, ~USD 1/key/month
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = true # reachable only through the firewall rules below
  tags                          = var.tags

  network_acls {
    default_action             = "Deny"
    bypass                     = "AzureServices" # Storage (CMK) and Data Factory are trusted services
    virtual_network_subnet_ids = var.allowed_subnet_ids
    ip_rules                   = var.allowed_ip_ranges
  }
}

# The identity running Terraform manages keys and secrets through RBAC, not access policies.
resource "azurerm_role_assignment" "deployer_crypto" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Crypto Officer"
  principal_id         = var.deployer_object_id
}

resource "azurerm_role_assignment" "deployer_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.deployer_object_id
}

resource "azurerm_key_vault_key" "storage_cmk" {
  name            = "cmk-lake"
  key_vault_id    = azurerm_key_vault.this.id
  key_type        = "RSA-HSM"
  key_size        = 3072
  key_opts        = ["unwrapKey", "wrapKey"]
  expiration_date = var.key_expiration_date
  tags            = var.tags

  rotation_policy {
    expire_after         = "P2Y"
    notify_before_expiry = "P30D"
    automatic {
      time_before_expiry = "P60D"
    }
  }

  depends_on = [azurerm_role_assignment.deployer_crypto]
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  name                       = "to-log-analytics"
  target_resource_id         = azurerm_key_vault.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  enabled_log {
    category_group = "audit"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}
