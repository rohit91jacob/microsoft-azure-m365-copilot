variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "project" {
  description = "Project name used in resource names and tags."
  type        = string
  default     = "azm365"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.project))
    error_message = "2-10 lowercase letters/digits, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name (dev, prod, ...)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "environment must be dev, test or prod."
  }
}

variable "location" {
  description = "Azure region. Must offer Flex Consumption and zone-redundant storage."
  type        = string
  default     = "swedencentral"
}

variable "cost_center" {
  description = "Value of the cost_center tag."
  type        = string
  default     = "portfolio"
}

variable "extra_tags" {
  description = "Additional tags merged into every resource."
  type        = map(string)
  default     = {}
}

variable "vnet_address_space" {
  description = "Address space of the integration VNet."
  type        = string
  default     = "10.42.0.0/24"
}

variable "admin_ip_ranges" {
  description = "Public IPs/CIDRs of the Terraform runner, admitted by the storage and Key Vault firewalls."
  type        = list(string)
  default     = []
}

variable "lake_replication_type" {
  description = "Lake storage redundancy."
  type        = string
  default     = "GZRS"

  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "GZRS", "RAGRS", "RAGZRS"], var.lake_replication_type)
    error_message = "Unsupported replication type."
  }
}

variable "function_maximum_instances" {
  description = "Scale-out ceiling of the Function App."
  type        = number
  default     = 40
}

variable "log_daily_quota_gb" {
  description = "Daily ingestion cap for logs and telemetry (cost guardrail)."
  type        = number
  default     = 0.1
}

variable "monthly_budget" {
  description = "Monthly budget for the resource group, in the billing currency."
  type        = number
  default     = 25
}

variable "budget_start_date" {
  description = "First month of the budget, e.g. 2026-11-01T00:00:00Z."
  type        = string
}

variable "alert_emails" {
  description = "Recipients of budget and failure alerts."
  type        = list(string)

  validation {
    condition     = length(var.alert_emails) > 0
    error_message = "At least one alert email is required."
  }
}

variable "key_expiration_date" {
  description = "RFC 3339 expiry for the CMK version and Key Vault secrets Terraform creates."
  type        = string
}

variable "github_token" {
  description = "Optional GitHub token stored in Key Vault (ephemeral: never written to state)."
  type        = string
  default     = null
  sensitive   = true
  ephemeral   = true
}

variable "github_token_version" {
  description = "0 = no GitHub token; bump to 1, 2, ... to write or rotate the secret."
  type        = number
  default     = 0
}

variable "github_repository" {
  description = "owner/name allowed to deploy through GitHub OIDC; empty to skip the deploy identity."
  type        = string
  default     = ""
}
