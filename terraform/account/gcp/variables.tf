variable "project_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Supply an existing GCP project ID."
  }
}
variable "state_bucket_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "Supply the existing private GCS state bucket name."
  }
}
variable "github_repository_id" {
  type = string
  validation {
    condition     = can(regex("^[1-9][0-9]+$", var.github_repository_id))
    error_message = "Use the numeric GitHub repository ID."
  }
}
variable "github_owner_id" {
  type = string
  validation {
    condition     = can(regex("^[1-9][0-9]+$", var.github_owner_id))
    error_message = "Use the numeric GitHub repository owner ID."
  }
}
