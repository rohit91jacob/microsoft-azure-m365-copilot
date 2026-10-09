terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.9, < 6.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 3.8, < 4.0"
    }
  }
}
