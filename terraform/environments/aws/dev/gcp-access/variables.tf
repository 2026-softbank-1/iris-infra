variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id)) && var.aws_account_id != "000000000000"
    error_message = "Supply the actual ECR AWS account ID."
  }
}
variable "gcp_service_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{21}$", var.gcp_service_account_id))
    error_message = "Use the numeric unique ID of the dedicated GCP ECR service account."
  }
}
variable "audience" {
  type    = string
  default = "iris-gcp-ecr-pull"
}
variable "enable_github_publisher" {
  type    = bool
  default = false
}
variable "github_oidc_subject_prefix" {
  type    = string
  default = ""
  validation {
    condition     = var.github_oidc_subject_prefix == "" || can(regex("^repo:[A-Za-z0-9_.-]+(@[0-9]+)?/[A-Za-z0-9_.-]+(@[0-9]+)?$", var.github_oidc_subject_prefix))
    error_message = "Use the actual main-branch subject prefix from GitHub OIDC settings."
  }
}
