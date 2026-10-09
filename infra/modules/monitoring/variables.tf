variable "name" {
  description = "Name stem for monitoring resources."
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

variable "retention_days" {
  description = "Log Analytics retention in days (30 is included in the ingestion price)."
  type        = number
  default     = 30
}

variable "daily_quota_gb" {
  description = "Daily ingestion cap for Log Analytics and Application Insights, in GB."
  type        = number
  default     = 0.1
}

variable "alert_emails" {
  description = "Email addresses notified by the action group."
  type        = list(string)
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
}
