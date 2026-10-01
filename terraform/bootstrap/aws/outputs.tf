output "state_bucket_name" {
  description = "Terraform state를 저장할 S3 버킷명. 각 root의 backend.hcl에 사용합니다."
  value       = aws_s3_bucket.terraform_state.id
}

output "state_bucket_arn" {
  description = "Terraform state용 S3 버킷 ARN. 접근 정책에 사용합니다."
  value       = aws_s3_bucket.terraform_state.arn
}
