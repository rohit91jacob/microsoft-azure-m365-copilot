variable "name" {
  description = "Name stem for lake resources."
  type        = string
}

variable "account_name" {
  description = "Storage account name (3-24 lowercase letters and digits, globally unique)."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.account_name))
    error_message = "Storage account names are 3-24 lowercase letters and digits."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to deploy into."
  type        = string
}

variable "replication_type" {
  description = "Redundancy. GZRS: three zones in the region plus an asynchronous copy in the paired region."
  type        = string
  default     = "GZRS"
}

variable "soft_delete_days" {
  description = "Blob and container soft-delete retention."
  type        = number
  default     = 14
}

variable "cmk_key_versionless_id" {
  description = "Versionless Key Vault key ID used as the customer-managed key."
  type        = string
}

variable "cmk_key_resource_id" {
  description = "ARM resource ID of that key (role assignment scope)."
  type        = string
}

variable "allowed_subnet_ids" {
  description = "Subnets admitted by the storage firewall."
  type        = list(string)
}

variable "allowed_ip_ranges" {
  description = "Public IPs/CIDRs admitted by the storage firewall."
  type        = list(string)
  default     = []
}

variable "log_analytics_workspace_id" {
  description = "Workspace receiving blob audit logs."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
}
