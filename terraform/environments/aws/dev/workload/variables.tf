variable "aws_account_id" {
  description = "배포를 허용할 AWS 계정 ID. 예시를 실제 계정으로 교체합니다."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id)) && var.aws_account_id != "000000000000"
    error_message = "실제 12자리 AWS 계정 ID가 필요합니다."
  }
}

variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "project" {
  description = "자원 이름·태그에 사용하는 프로젝트 이름"
  type        = string
  default     = "iris"
}

variable "environment" {
  description = "환경 이름"
  type        = string
  default     = "dev"
}


variable "operator_principal_arn" {
  description = "Explicit existing operator IAM user/role ARN; no default."
  type        = string
}
variable "state_bucket_name" {
  description = "Empty means the standard bootstrap bucket name."
  type        = string
  default     = ""
}
