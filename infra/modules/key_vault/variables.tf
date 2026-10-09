variable "name" {
  description = "Key Vault name (3-24 characters, globally unique)."
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

variable "tenant_id" {
  description = "Entra ID tenant of the vault."
  type        = string
}

variable "deployer_object_id" {
  description = "Object ID of the identity running Terraform (granted crypto and secrets officer)."
  type        = string
}

variable "allowed_subnet_ids" {
  description = "Subnets admitted by the vault firewall."
  type        = list(string)
}

variable "allowed_ip_ranges" {
  description = "Public IPs/CIDRs admitted by the vault firewall (the Terraform runner)."
  type        = list(string)
  default     = []
}

variable "key_expiration_date" {
  description = "RFC 3339 expiry of the storage CMK version created by Terraform (rotation renews it)."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Workspace receiving the vault's audit logs."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
}
