provider "azurerm" {
  features {
    key_vault {
      # Keep soft-deleted vaults and keys recoverable; never purge on destroy.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }
  subscription_id = var.subscription_id
  # Data-plane calls (containers, queues) use Entra ID, matching shared_access_key_enabled = false.
  storage_use_azuread = true
}

provider "azuread" {}
