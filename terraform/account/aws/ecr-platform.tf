# Keep platform ECR management separate from state and CodeBuild resource permissions.
locals {
  platform_ecr_suffixes = jsondecode(file("${path.module}/../../config/platform-ecr-repositories.json"))
  platform_ecr_names = {
    for suffix in local.platform_ecr_suffixes : suffix => "${var.project}/${suffix}"
  }
  platform_ecr_arns = {
    for suffix, name in local.platform_ecr_names :
    suffix => "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${name}"
  }
}

resource "aws_iam_role_policy" "platform_ecr" {
  name = "platform-ecr-resources"
  role = aws_iam_role.terraform_apply.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "ManagePlatformRepositories"
      Effect = "Allow"
      Action = [
        "ecr:CreateRepository",
        "ecr:DescribeRepositories",
        "ecr:DeleteRepository",
        "ecr:ListTagsForResource",
        "ecr:TagResource",
        "ecr:UntagResource",
        "ecr:PutImageTagMutability",
        "ecr:PutImageScanningConfiguration",
        "ecr:GetLifecyclePolicy",
        "ecr:PutLifecyclePolicy",
        "ecr:DeleteLifecyclePolicy",
      ]
      Resource = values(local.platform_ecr_arns)
    }]
  })
}

variable "github_ecr_publishers" {
  description = "Service repository publishers: actual GitHub OIDC prefix and allowed full ECR repository names. Account administrators apply these roles."
  type = map(object({
    oidc_subject_prefix = string
    repository_names    = set(string)
  }))
  default = {}

  validation {
    condition = alltrue([
      for key in keys(var.github_ecr_publishers) : can(regex("^[a-z0-9][a-z0-9-]{0,23}$", key))
    ])
    error_message = "Publisher identifiers must be 1-24 lowercase letters, digits or hyphens."
  }

  validation {
    condition = alltrue([
      for publisher in values(var.github_ecr_publishers) :
      can(regex("^repo:[A-Za-z0-9_.-]+(@[0-9]+)?/[A-Za-z0-9_.-]+(@[0-9]+)?$", publisher.oidc_subject_prefix))
    ])
    error_message = "Use the actual repo:OWNER/REPO or repo:OWNER@ID/REPO@ID subject prefix, without a branch suffix."
  }

  validation {
    condition = alltrue([
      for publisher in values(var.github_ecr_publishers) :
      length(publisher.repository_names) > 0 &&
      alltrue([for name in publisher.repository_names : contains(values(local.platform_ecr_names), name)])
    ])
    error_message = "Each publisher needs at least one repository from the shared platform ECR inventory."
  }
}

resource "aws_iam_role" "ecr_publisher" {
  for_each = var.github_ecr_publishers

  name        = "${var.project}-${var.environment}-github-ecr-${each.key}"
  description = "GitHub Actions main-branch publisher for ${each.key} platform images"

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
          "token.actions.githubusercontent.com:sub" = "${each.value.oidc_subject_prefix}:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ecr_publisher" {
  for_each = var.github_ecr_publishers

  name = "push-platform-images"
  role = aws_iam_role.ecr_publisher[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:RequestedRegion" = var.aws_region }
        }
      },
      {
        Sid    = "PushPlatformImages"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
        ]
        Resource = [
          for name in sort(tolist(each.value.repository_names)) :
          "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${name}"
        ]
      },
    ]
  })
}

output "github_ecr_publisher_role_arns" {
  description = "Set each service repository's ECR_PUSH_ROLE_ARN variable to its publisher role ARN."
  value       = { for key, role in aws_iam_role.ecr_publisher : key => role.arn }
}
