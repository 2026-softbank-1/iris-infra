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
