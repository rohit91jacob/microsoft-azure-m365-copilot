terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.9"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.8"
    }
  }

  # Remote state for a real deployment (Azure Storage, Entra ID auth, blob lease locking).
  # Supply the settings with `terraform init -backend-config=environments/dev.azurerm.tfbackend`.
  # CI only runs `terraform init -backend=false` + validate: nothing is applied from CI.
  backend "azurerm" {}
}
