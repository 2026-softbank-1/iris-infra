variable "project_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Supply an existing, billing-enabled GCP project ID."
  }
}
variable "state_bucket_name" {
  type = string
}
