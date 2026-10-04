output "pull_role_arn" { value = aws_iam_role.pull.arn }
output "credentials_repository" { value = aws_ecr_repository.credentials.repository_url }
output "github_publisher_role_arn" { value = try(aws_iam_role.publisher[0].arn, null) }
