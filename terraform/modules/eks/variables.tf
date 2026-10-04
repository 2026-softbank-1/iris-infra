variable "aws_account_id" {
  description = "aws_account_id"
  type        = string
}

variable "aws_region" {
  description = "aws_region"
  type        = string
}

variable "cluster_name" {
  description = "cluster_name"
  type        = string
}

variable "vpc_id" {
  description = "vpc_id"
  type        = string
}

variable "subnet_ids_by_az" {
  description = "subnet_ids_by_az"
  type        = map(string)
  validation {
    condition     = length(var.subnet_ids_by_az) == 2 && length(distinct(values(var.subnet_ids_by_az))) == 2
    error_message = "Two distinct private subnets, one per AZ, are required."
  }
}

variable "service_cidr" {
  description = "service_cidr"
  type        = string
  validation {
    condition     = contains(["172.20.0.0/16", "172.21.0.0/16"], var.service_cidr)
    error_message = "Use the reviewed, distinct management/workload service ranges."
  }
}

variable "operator_principal_arn" {
  description = "operator_principal_arn"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::${var.aws_account_id}:(role|user)/.+$", var.operator_principal_arn))
    error_message = "Provide an IAM role/user ARN in the deployment account, not an STS session ARN."
  }
}

variable "additional_operator_principal_arns" {
  description = "Extra operator IAM user/role ARNs that receive the same cluster-admin Access Entry"
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for arn in var.additional_operator_principal_arns : can(regex("^arn:aws:iam::${var.aws_account_id}:(role|user)/.+$", arn))]) && length(distinct(var.additional_operator_principal_arns)) == length(var.additional_operator_principal_arns) && !contains(var.additional_operator_principal_arns, var.operator_principal_arn)
    error_message = "Provide distinct IAM role/user ARNs in the deployment account, excluding operator_principal_arn."
  }
}

variable "argocd_deploy_role_arn" {
  description = "argocd_deploy_role_arn"
  type        = string
}

variable "console_gateway_role_arn" {
  description = "Console Gateway IAM role that may exec into user service Pods through Kubernetes group iris-console. Empty means no Access Entry (management)."
  type        = string
  default     = ""
  validation {
    condition     = var.console_gateway_role_arn == "" || can(regex("^arn:aws:iam::${var.aws_account_id}:role/.+$", var.console_gateway_role_arn))
    error_message = "Provide an IAM role ARN in the deployment account, or leave empty."
  }
}

variable "bridge_security_group_id" {
  description = "bridge_security_group_id"
  type        = string
}

variable "additional_api_security_group_ids" {
  description = "additional_api_security_group_ids"
  type        = list(string)
  default     = []
}

variable "additional_node_security_group_ids" {
  description = "additional_node_security_group_ids"
  type        = list(string)
  default     = []
}

variable "kubernetes_version" {
  description = "kubernetes_version"
  type        = string
  default     = "1.35"
  validation {
    condition     = var.kubernetes_version == "1.35"
    error_message = "This implementation is validated for EKS 1.35."
  }
}

variable "node_instance_type" {
  description = "node_instance_type"
  type        = string
  default     = "m7i-flex.large"
  validation {
    condition     = var.node_instance_type == "m7i-flex.large"
    error_message = "The reviewed Free account configuration uses m7i-flex.large."
  }
}

variable "node_count_by_az" {
  description = "AZ별 고정 노드 수. 적지 않은 AZ 는 1 대다."
  type        = map(number)
  default     = {}
  validation {
    condition     = alltrue([for az, count in var.node_count_by_az : contains(keys(var.subnet_ids_by_az), az) && count >= 1 && count <= 2])
    error_message = "node_count_by_az keys must be AZs in subnet_ids_by_az and values 1 or 2."
  }
}

variable "node_release_version" {
  description = "node_release_version"
  type        = string
  default     = "1.35.8-20260930"
}

variable "tags" {
  description = "tags"
  type        = map(string)
  default     = {}
}
