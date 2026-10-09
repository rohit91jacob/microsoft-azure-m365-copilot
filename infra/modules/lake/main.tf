# ADLS Gen2 lake: one StorageV2 account with hierarchical namespace and the four medallion
# containers. Entra ID only (no shared keys, no SAS), customer-managed key from Key Vault with
# infrastructure (double) encryption, firewall closed except the Function subnet and trusted
# Azure services (Data Factory), soft delete, and lifecycle tiering to keep storage cheap.

resource "azurerm_user_assigned_identity" "cmk" {
  name                = "id-${var.name}-cmk"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

# Least privilege: the storage identity may only wrap/unwrap with this one key.
resource "azurerm_role_assignment" "cmk_key_user" {
  scope                = var.cmk_key_resource_id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_user_assigned_identity.cmk.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_storage_account" "lake" {
  #checkov:skip=CKV_AZURE_59:Public endpoint stays enabled only so service endpoints work; network_rules default to Deny (Function subnet + trusted services only).
  #checkov:skip=CKV2_AZURE_33:Service endpoints instead of private endpoints (~USD 7/month each, plus private DNS); see README cost notes.
  #checkov:skip=CKV_AZURE_33:The lake has no queues; queue logging is configured where queues exist (Functions host storage).
  name                     = var.account_name
  location                 = var.location
  resource_group_name      = var.resource_group_name
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = var.replication_type
  access_tier              = "Hot"
  is_hns_enabled           = true

  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  cross_tenant_replication_enabled  = false
  local_user_enabled                = false
  sftp_enabled                      = false
  infrastructure_encryption_enabled = true
  allowed_copy_scope                = "AAD"
  public_network_access             = "Enabled" # gated by network_rules below
  tags                              = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.cmk.id]
  }

  customer_managed_key {
    key_vault_key_id          = var.cmk_key_versionless_id
    user_assigned_identity_id = azurerm_user_assigned_identity.cmk.id
  }

  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices", "Logging", "Metrics"]
    virtual_network_subnet_ids = var.allowed_subnet_ids
    ip_rules                   = var.allowed_ip_ranges
  }

  blob_properties {
    # Blob versioning is not available with a hierarchical namespace; soft delete protects
    # against accidental deletes and bronze is append-only by design.
    delete_retention_policy {
      days = var.soft_delete_days
    }
    container_delete_retention_policy {
      days = var.soft_delete_days
    }
  }

  sas_policy {
    expiration_period = "00.01:00:00"
    expiration_action = "Block"
  }

  depends_on = [azurerm_role_assignment.cmk_key_user]
}

resource "azurerm_storage_container" "this" {
  #checkov:skip=CKV2_AZURE_21:Blob read/write/delete logging is enabled on the account's blob service (azurerm_monitor_diagnostic_setting.blob), which covers every container.
  for_each              = toset(["bronze", "silver", "gold", "ops"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "lake" {
  storage_account_id = azurerm_storage_account.lake.id

  rule {
    name    = "bronze-tiering"
    enabled = true
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["bronze/azure_prices/", "bronze/m365_roadmap/", "bronze/github/"]
    }
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = 30
        tier_to_cold_after_days_since_modification_greater_than = 90
      }
    }
  }

  rule {
    name    = "ops-expiry"
    enabled = true
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["ops/dq_results/"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 400
      }
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "blob" {
  name                       = "to-log-analytics"
  target_resource_id         = "${azurerm_storage_account.lake.id}/blobServices/default"
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
