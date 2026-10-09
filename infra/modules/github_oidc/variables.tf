variable "name" {
  description = "Name stem for the deploy identity."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the repository allowed to deploy."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use the owner/name form."
  }
}

variable "github_environment" {
  description = "GitHub environment whose jobs may assume the identity."
  type        = string
  default     = "production"
}

variable "function_app_id" {
  description = "Function App the identity may deploy to (Website Contributor scope)."
  type        = string
}
