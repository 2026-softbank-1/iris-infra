variable "project_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Supply an existing GCP project ID."
  }
}
variable "management_oidc_issuer" {
  type = string
  validation {
    condition     = can(regex("^https://oidc\\.eks\\.ap-northeast-2\\.amazonaws\\.com/id/[A-Za-z0-9]+$", var.management_oidc_issuer))
    error_message = "Use the actual AWS management EKS public OIDC issuer."
  }
}
variable "base_domain" {
  type    = string
  default = "gcp.likelion.uk"
  validation {
    condition     = can(regex("^[a-z0-9-]+(\\.[a-z0-9-]+)+$", var.base_domain))
    error_message = "Use a domain without wildcard or scheme."
  }
}
variable "node_machine_type" {
  type    = string
  default = "e2-standard-2"
}
variable "vpc_cidr" {
  type    = string
  default = "10.60.0.0/20"
}
variable "pod_cidr" {
  type    = string
  default = "10.64.0.0/16"
}
variable "service_cidr" {
  type    = string
  default = "172.22.0.0/20"
}
variable "ecr_audience" {
  type    = string
  default = "iris-gcp-ecr-pull"
}
