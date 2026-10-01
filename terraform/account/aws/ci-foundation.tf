# Match the build resources currently implemented in foundation/build.tf.
# Future VPC/EKS resources require their own deployment permissions.
locals {
  build_name           = "${var.project}-${var.environment}-build"
  build_bucket_arn     = "arn:aws:s3:::${local.build_name}-artifacts-${var.aws_account_id}-${var.aws_region}"
  build_project_arn    = "arn:aws:codebuild:${var.aws_region}:${var.aws_account_id}:project/${local.build_name}"
  build_log_group_arn  = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/codebuild/${local.build_name}"
  build_codebuild_role = "arn:aws:iam::${var.aws_account_id}:role/${local.build_name}-codebuild"
  build_worker_role    = "arn:aws:iam::${var.aws_account_id}:role/${local.build_name}-worker"
}

resource "aws_iam_role_policy" "foundation" {
  name = "foundation-build-resources"
  role = aws_iam_role.terraform_apply.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadBuildBucketConfiguration"
        Effect = "Allow"
        # aws_s3_bucket refreshes these configurations even when separate resources own them.
        Action = [
          "s3:ListBucket",
          "s3:ListBucketVersions",
          "s3:GetBucket*",
          "s3:GetAccelerateConfiguration",
          "s3:GetEncryptionConfiguration",
          "s3:GetLifecycleConfiguration",
          "s3:GetReplicationConfiguration",
          "s3:ListTagsForResource",
        ]
        Resource = local.build_bucket_arn
      },
      {
        Sid    = "ManageBuildBucket"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:DeleteBucket",
          "s3:PutBucketTagging",
          "s3:TagResource",
          "s3:UntagResource",
          "s3:PutEncryptionConfiguration",
          "s3:PutBucketPublicAccessBlock",
          "s3:PutLifecycleConfiguration",
          "s3:PutBucketPolicy",
          "s3:DeleteBucketPolicy",
        ]
        Resource = local.build_bucket_arn
      },
      {
        Sid    = "EmptyBuildBucketOnReplacement"
        Effect = "Allow"
        # This bucket uses force_destroy; state objects are in a different bucket.
        Action   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource = "${local.build_bucket_arn}/*"
      },
      {
        Sid    = "ManageBuildProject"
        Effect = "Allow"
        # CreateProject/UpdateProject also manage CodeBuild tags; there is no TagResource API.
        Action   = ["codebuild:CreateProject", "codebuild:BatchGetProjects", "codebuild:UpdateProject", "codebuild:DeleteProject"]
        Resource = local.build_project_arn
      },
      {
        Sid    = "ManageBuildRoles"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:GetRole",
          "iam:UpdateRole",
          "iam:UpdateRoleDescription",
          "iam:UpdateAssumeRolePolicy",
          "iam:DeleteRole",
          "iam:ListRoleTags",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:ListRolePolicies",
          "iam:GetRolePolicy",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:ListAttachedRolePolicies",
          "iam:DetachRolePolicy",
          "iam:ListInstanceProfilesForRole",
        ]
        Resource = [local.build_codebuild_role, local.build_worker_role]
      },
      {
        Sid      = "PassCodeBuildServiceRole"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = local.build_codebuild_role
        Condition = {
          StringEquals = { "iam:PassedToService" = "codebuild.amazonaws.com" }
        }
      },
      {
        Sid      = "FindBuildLogGroup"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:RequestedRegion" = var.aws_region }
        }
      },
      {
        Sid    = "ManageBuildLogGroup"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:PutRetentionPolicy",
          "logs:DeleteRetentionPolicy",
          "logs:PutLogGroupDeletionProtection",
          "logs:ListTagsForResource",
          "logs:TagResource",
          "logs:UntagResource",
          "logs:TagLogGroup",
          "logs:UntagLogGroup",
        ]
        # TagResource uses the ARN without :*, while other log-group APIs use the suffix.
        Resource = [local.build_log_group_arn, "${local.build_log_group_arn}:*"]
      },
    ]
  })
}
