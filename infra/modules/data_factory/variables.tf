variable "name" {
  description = "Name stem for Data Factory resources."
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

variable "orchestration_dir" {
  description = "Directory holding the exported ADF pipeline and trigger JSON."
  type        = string
}

variable "lake_account_id" {
  description = "Lake storage account ID (Storage Blob Data Reader scope)."
  type        = string
}

variable "lake_dfs_endpoint" {
  description = "Lake ADLS Gen2 (dfs) endpoint."
  type        = string
}

variable "key_vault_id" {
  description = "Key Vault holding the Function host key."
  type        = string
}

variable "function_app_name" {
  description = "Function App whose transform endpoint the pipeline calls."
  type        = string
}

variable "function_app_hostname" {
  description = "Function App default hostname."
  type        = string
}

variable "function_key_version" {
  description = "Bump to rewrite the host key secret (write-only attribute versioning)."
  type        = number
  default     = 1
}

variable "secret_expiration_date" {
  description = "RFC 3339 expiry of the host key secret."
  type        = string
}

variable "trigger_activated" {
  description = "Start the daily schedule trigger."
  type        = bool
  default     = true
}

variable "log_analytics_workspace_id" {
  description = "Workspace receiving pipeline, activity and trigger run logs."
  type        = string
}

variable "action_group_id" {
  description = "Action group notified on failed pipeline runs."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
}
