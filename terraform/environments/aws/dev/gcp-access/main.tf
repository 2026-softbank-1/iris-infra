resource "aws_ecr_repository" "credentials" {
  name                 = "iris/gcp-ecr-credentials"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  lifecycle { prevent_destroy = true }
}
resource "aws_iam_role" "pull" {
  name = "iris-dev-gcp-ecr-pull"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = "accounts.google.com" }
      Condition = { StringEquals = {
        "accounts.google.com:aud"  = var.gcp_service_account_id
        "accounts.google.com:oaud" = var.audience
        "accounts.google.com:sub"  = var.gcp_service_account_id
      } }
    }]
  })
}
resource "aws_iam_role_policy" "pull" {
  name = "read-iris-images"
  role = aws_iam_role.pull.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = ["arn:aws:ecr:ap-northeast-2:${var.aws_account_id}:repository/iris/services/*", aws_ecr_repository.credentials.arn] }
    ]
  })
}
