# Administrators apply account before foundation. This policy never grants log-object writes.
locals {
  alb_collector_role_arn = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-alb-log-collector"
  alb_logs_bucket_arn    = "arn:aws:s3:::${var.project}-${var.environment}-alb-access-logs-${var.aws_account_id}-${var.aws_region}"
  alb_logs_queue_arn     = "arn:aws:sqs:${var.aws_region}:${var.aws_account_id}:${var.project}-${var.environment}-alb-access-logs"
}

resource "aws_iam_policy" "alb_access_logs" {
  name = "${var.project}-${var.environment}-alb-access-logs-deployment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "CollectorRole", Effect = "Allow", Action = ["iam:CreateRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:DeleteRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags"], Resource = local.alb_collector_role_arn },
    { Sid = "PassCollectorRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.alb_collector_role_arn, Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } },
    { Sid = "ReadBucketConfiguration", Effect = "Allow", Action = ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucket*", "s3:GetAccelerateConfiguration", "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:GetReplicationConfiguration", "s3:ListTagsForResource"], Resource = local.alb_logs_bucket_arn },
    { Sid = "ManageLogBucket", Effect = "Allow", Action = ["s3:CreateBucket", "s3:DeleteBucket", "s3:PutBucketTagging", "s3:TagResource", "s3:UntagResource", "s3:PutEncryptionConfiguration", "s3:PutBucketPublicAccessBlock", "s3:PutLifecycleConfiguration", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutBucketNotification"], Resource = local.alb_logs_bucket_arn },
    { Sid = "ManageQueues", Effect = "Allow", Action = ["sqs:CreateQueue", "sqs:DeleteQueue", "sqs:GetQueueUrl", "sqs:GetQueueAttributes", "sqs:SetQueueAttributes", "sqs:TagQueue", "sqs:UntagQueue", "sqs:ListQueueTags"], Resource = [local.alb_logs_queue_arn, "${local.alb_logs_queue_arn}-dlq"], Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } } }
  ] })
}

resource "aws_iam_role_policy_attachment" "alb_access_logs" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.alb_access_logs.arn
}
