variable "name" {
  description = "Name stem for Function App resources."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to deploy into."
  type        = string
}

variable "host_storage_account_name" {
  description = "Name of the Functions host/deployment storage account (3-24 lowercase alphanumerics)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.host_storage_account_name))
    error_message = "Storage account names are 3-24 lowercase letters and digits."
  }
}

variable "subnet_id" {
  description = "Delegated subnet for VNet integration."
  type        = string
}

variable "allowed_ip_ranges" {
  description = "Public IPs/CIDRs admitted by the host storage firewall."
  type        = list(string)
  default     = []
}

variable "maximum_instance_count" {
  description = "Scale-out ceiling for the Flex Consumption app (cost guardrail)."
  type        = number
  default     = 40
}

variable "lake_account_id" {
  description = "Lake storage account ID (Storage Blob Data Contributor scope)."
  type        = string
}

variable "lake_blob_endpoint" {
  description = "Lake blob endpoint passed to the pipeline."
  type        = string
}

variable "key_vault_id" {
  description = "Key Vault ID (Key Vault Secrets User scope)."
  type        = string
}

variable "github_token_secret_uri" {
  description = "Versionless Key Vault secret URI of the optional GitHub token; empty to run anonymously."
  type        = string
  default     = ""
}

variable "application_insights_id" {
  description = "Application Insights resource ID."
  type        = string
}

variable "application_insights_connection_string" {
  description = "Application Insights connection string."
  type        = string
  sensitive   = true
}

variable "log_analytics_workspace_id" {
  description = "Workspace receiving Function App logs."
  type        = string
}

variable "action_group_id" {
  description = "Action group notified on function failures."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
}
