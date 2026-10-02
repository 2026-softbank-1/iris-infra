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

variable "vpc_cidr" {
  description = "공유 VPC의 정규 IPv4 /16 CIDR. 기존 VPC·VPN·서비스 CIDR과 중복되지 않아야 합니다."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition = try(
      length(split(".", cidrhost(var.vpc_cidr, 0))) == 4 &&
      split("/", var.vpc_cidr)[1] == "16" &&
      "${cidrhost(var.vpc_cidr, 0)}/16" == var.vpc_cidr,
      false
    )
    error_message = "vpc_cidr은 network address로 시작하는 IPv4 /16 CIDR이어야 합니다."
  }
}

variable "availability_zones" {
  description = "서브넷 슬롯 0·1에 대응하는 서로 다른 AZ 2개. 순서·값 변경은 서브넷 교체를 유발합니다."
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2c"]

  validation {
    condition = (
      length(var.availability_zones) == 2 &&
      length(distinct(var.availability_zones)) == 2 &&
      alltrue([for az in var.availability_zones : can(regex("^${var.aws_region}[a-z]$", az))])
    )
    error_message = "availability_zones에는 aws_region에 속하는 서로 다른 표준 AZ 2개를 지정하세요."
  }
}

variable "nat_gateway_mode" {
  description = "single: 슬롯 0의 zonal NAT 공유, per_az: 각 AZ의 zonal NAT 사용. 실제 적용 시 비용이 발생합니다."
  type        = string
  default     = "per_az"

  validation {
    condition     = contains(["single", "per_az"], var.nat_gateway_mode)
    error_message = "nat_gateway_mode는 single 또는 per_az여야 합니다."
  }
}

variable "management_cluster_name" {
  description = "후속 관리 EKS가 사용할 이름. subnet discovery 태그와 일치해야 합니다."
  type        = string
  default     = "iris-dev-management"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$", var.management_cluster_name))
    error_message = "management_cluster_name은 EKS 클러스터 이름 형식을 따라야 합니다."
  }
}

variable "workload_cluster_name" {
  description = "후속 앱 EKS가 사용할 이름. 관리 클러스터와 다른 이름이어야 합니다."
  type        = string
  default     = "iris-dev-workload"

  validation {
    condition = (
      can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$", var.workload_cluster_name)) &&
      var.workload_cluster_name != var.management_cluster_name
    )
    error_message = "workload_cluster_name은 유효한 EKS 이름이며 management_cluster_name과 달라야 합니다."
  }
}

variable "domain_name" {
  description = "사용자 서비스 도메인. 기존 Route53 hosted zone 이름과 같아야 합니다."
  type        = string
  default     = "likelion.uk"
}
