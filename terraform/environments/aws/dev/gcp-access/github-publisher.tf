# account/aws already owns the GitHub provider. Never create a duplicate here.
resource "aws_iam_role" "publisher" {
  count                = var.enable_github_publisher ? 1 : 0
  name                 = "iris-dev-gcp-credentials-publisher"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = "arn:aws:iam::${var.aws_account_id}:oidc-provider/token.actions.githubusercontent.com" }
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix}:ref:refs/heads/main"
      } }
    }]
  })
  lifecycle {
    precondition {
      condition     = var.github_oidc_subject_prefix != ""
      error_message = "Read the actual repository OIDC subject prefix before enabling publisher."
    }
  }
}
resource "aws_iam_role_policy" "publisher" {
  count = var.enable_github_publisher ? 1 : 0
  name  = "publish-gcp-credentials-only"
  role  = aws_iam_role.publisher[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      { Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:ListImages", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = aws_ecr_repository.credentials.arn }
    ]
  })
}
