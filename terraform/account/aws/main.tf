locals {
  state_bucket_name = "${var.project}-tfstate-${var.aws_account_id}-${var.aws_region}"
  state_bucket_arn  = "arn:aws:s3:::${local.state_bucket_name}"
  deployment_state_keys = [
    "bootstrap/aws/terraform.tfstate",
    "aws/dev/foundation/terraform.tfstate",
    "aws/dev/management/terraform.tfstate",
    "aws/dev/workload/terraform.tfstate",
  ]
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "terraform_apply" {
  max_session_duration = 7200
  name                 = "${var.project}-${var.environment}-github-terraform"
  description          = "GitHub Actions main-branch deployment of bootstrap and implemented dev stacks"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix}:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "terraform_apply" {
  name = "bootstrap-state-bucket"
  role = aws_iam_role.terraform_apply.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadBucketConfiguration"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucket*",
          "s3:GetAccelerateConfiguration",
          "s3:GetEncryptionConfiguration",
          "s3:GetLifecycleConfiguration",
          "s3:GetReplicationConfiguration",
          "s3:ListTagsForResource",
        ]
        Resource = local.state_bucket_arn
      },
      {
        Sid    = "ManageBootstrapBucket"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:PutBucketVersioning",
          "s3:PutEncryptionConfiguration",
          "s3:PutBucketPublicAccessBlock",
          "s3:PutBucketTagging",
          "s3:TagResource",
          "s3:UntagResource",
        ]
        Resource = local.state_bucket_arn
      },
      {
        Sid      = "ReadAndWriteDeploymentState"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = [for key in local.deployment_state_keys : "${local.state_bucket_arn}/${key}"]
      },
      {
        Sid      = "ManageDeploymentStateLocks"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [for key in local.deployment_state_keys : "${local.state_bucket_arn}/${key}.tflock"]
      },
    ]
  })
}
