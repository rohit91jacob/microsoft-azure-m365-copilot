variable "name" {
  description = "Name stem for the budget."
  type        = string
}

variable "resource_group_id" {
  description = "Resource group the budget covers."
  type        = string
}

variable "amount" {
  description = "Monthly budget in the billing currency."
  type        = number

  validation {
    condition     = var.amount > 0
    error_message = "The budget amount must be positive."
  }
}

variable "start_date" {
  description = "First day of the first budget month, RFC 3339 (e.g. 2026-11-01T00:00:00Z)."
  type        = string

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-01T00:00:00Z$", var.start_date))
    error_message = "Budgets start at 00:00 UTC on the first day of a month."
  }
}

variable "actual_thresholds" {
  description = "Percentages of actual spend that trigger a notification."
  type        = list(number)
  default     = [50, 80, 100]
}

variable "contact_emails" {
  description = "Emails notified by the budget."
  type        = list(string)
}

variable "action_group_id" {
  description = "Action group notified by the budget."
  type        = string
}
