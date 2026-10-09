variable "name" {
  description = "Name stem for network resources."
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

variable "address_space" {
  description = "VNet address space (a /24 or larger)."
  type        = string
  default     = "10.42.0.0/24"
}

variable "default_outbound_access_enabled" {
  description = "Keep Azure default outbound internet access on the function subnet (no NAT gateway)."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
}
