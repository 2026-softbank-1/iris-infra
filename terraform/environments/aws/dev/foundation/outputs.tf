# 후속 stack·iris-was 설정에 전달할 비밀값이 아닌 출력만 정의합니다.
# 계정 자격 증명, kubeconfig와 환경변수 실제 값은 출력하지 않습니다.

output "build_codebuild_project_name" {
  description = "iris-was CODEBUILD_PROJECT 값"
  value       = aws_codebuild_project.build.name
}

output "build_artifact_bucket_name" {
  description = "iris-was ARTIFACT_BUCKET 값"
  value       = aws_s3_bucket.build_artifacts.bucket
}

output "build_worker_role_arn" {
  description = "Build Worker Pod Identity 연결에 쓸 역할"
  value       = aws_iam_role.build_worker.arn
}

output "vpc_id" {
  description = "두 EKS stack가 공유할 VPC ID"
  value       = aws_vpc.shared.id
}

output "public_subnet_ids_by_az" {
  description = "외부 ALB용 subnet ID의 AZ별 map(string)"
  value       = { for slot, az in local.network_slots : az => aws_subnet.public[slot].id }
}

output "management_subnet_ids_by_az" {
  description = "관리 EKS control plane·노드용 사설 subnet ID의 AZ별 map(string)"
  value       = { for slot, az in local.network_slots : az => aws_subnet.private["management-${slot}"].id }
}

output "workload_subnet_ids_by_az" {
  description = "앱 EKS control plane·노드용 사설 subnet ID의 AZ별 map(string)"
  value       = { for slot, az in local.network_slots : az => aws_subnet.private["workload-${slot}"].id }
}

output "management_api_source_security_group_id" {
  description = "후속 management stack에서 Worker 송신 ENI에 연결할 추가 SG ID"
  value       = aws_security_group.management_api_source.id
}

output "workload_api_target_security_group_id" {
  description = "후속 workload stack에서 control plane에 연결할 추가 SG ID"
  value       = aws_security_group.workload_api_target.id
}

output "management_cluster_name" {
  description = "subnet discovery 태그와 일치하는 관리 EKS 이름"
  value       = var.management_cluster_name
}

output "workload_cluster_name" {
  description = "subnet discovery 태그와 일치하는 앱 EKS 이름"
  value       = var.workload_cluster_name
}
