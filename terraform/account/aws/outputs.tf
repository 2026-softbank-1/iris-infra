output "terraform_apply_role_arn" {
  description = "GitHub repository variable TERRAFORM_APPLY_ROLE_ARN에 설정할 역할 ARN."
  value       = aws_iam_role.terraform_apply.arn
}
