# Raw ALB logs are separate from Loki's chunks/index. LBC, not Terraform, owns the ALB.
locals {
  alb_logs_name   = "${var.project}-${var.environment}-alb-access-logs"
  alb_log_prefix  = "alb/workload"
  alb_log_objects = "${local.alb_log_prefix}/AWSLogs/${var.aws_account_id}/elasticloadbalancing/${var.aws_region}/"
  # Regions launched before August 2022 deliver ALB logs as a regional ELB account, not through the
  # logdelivery service principal; the service principal alone is rejected with "Access Denied for
  # bucket". Account IDs are published by AWS per region (ap-northeast-2 verified on 2026-10-03).
  alb_log_delivery_account_ids = { "ap-northeast-2" = "600734575887" }
  # Regions without an entry keep only the service principal statement.
  # Same prefix and write-only action as the service principal; the regional account cannot read or
  # list the bucket.
  alb_log_regional_delivery = [for region, account in local.alb_log_delivery_account_ids : {
    Sid       = "ALBLogDeliveryRegionalAccount"
    Effect    = "Allow"
    Principal = { AWS = "arn:aws:iam::${account}:root" }
    Action    = "s3:PutObject"
    Resource  = "${aws_s3_bucket.alb_access_logs.arn}/${local.alb_log_prefix}/AWSLogs/${var.aws_account_id}/*"
  } if region == var.aws_region]
}

resource "aws_s3_bucket" "alb_access_logs" {
  bucket        = "${local.alb_logs_name}-${var.aws_account_id}-${var.aws_region}"
  force_destroy = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_access_logs" {
  bucket = aws_s3_bucket.alb_access_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "alb_access_logs" {
  bucket                  = aws_s3_bucket.alb_access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_access_logs" {
  bucket = aws_s3_bucket.alb_access_logs.id
  rule {
    id     = "expire-raw-logs"
    status = "Enabled"
    filter { prefix = "${local.alb_log_prefix}/" }
    expiration { days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_s3_bucket_policy" "alb_access_logs" {
  bucket = aws_s3_bucket.alb_access_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Sid       = "ALBLogDelivery"
        Effect    = "Allow"
        Principal = { Service = "logdelivery.elasticloadbalancing.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.alb_access_logs.arn}/${local.alb_log_prefix}/AWSLogs/${var.aws_account_id}/*"
        Condition = { StringEquals = { "aws:SourceAccount" = var.aws_account_id }, ArnLike = {
          "aws:SourceArn" = "arn:aws:elasticloadbalancing:${var.aws_region}:${var.aws_account_id}:loadbalancer/app/iris-service-external/*"
        } }
      }],
      local.alb_log_regional_delivery,
      [{
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.alb_access_logs.arn, "${aws_s3_bucket.alb_access_logs.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }]
    )
  })
  depends_on = [aws_s3_bucket_public_access_block.alb_access_logs]
}

resource "aws_sqs_queue" "alb_access_logs_dead_letter" {
  name                      = "${local.alb_logs_name}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "alb_access_logs" {
  name                       = local.alb_logs_name
  message_retention_seconds  = 604800
  visibility_timeout_seconds = 120
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.alb_access_logs_dead_letter.arn, maxReceiveCount = 10 })
}

resource "aws_sqs_queue_redrive_allow_policy" "alb_access_logs" {
  queue_url = aws_sqs_queue.alb_access_logs_dead_letter.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue", sourceQueueArns = [aws_sqs_queue.alb_access_logs.arn]
  })
}

resource "aws_sqs_queue_policy" "alb_access_logs" {
  queue_url = aws_sqs_queue.alb_access_logs.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid       = "BucketNotifications", Effect = "Allow", Principal = { Service = "s3.amazonaws.com" },
    Action    = "sqs:SendMessage", Resource = aws_sqs_queue.alb_access_logs.arn,
    Condition = { ArnEquals = { "aws:SourceArn" = aws_s3_bucket.alb_access_logs.arn }, StringEquals = { "aws:SourceAccount" = var.aws_account_id } }
  }] })
}

resource "aws_s3_bucket_notification" "alb_access_logs" {
  bucket = aws_s3_bucket.alb_access_logs.id
  queue {
    queue_arn     = aws_sqs_queue.alb_access_logs.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = local.alb_log_objects
    filter_suffix = ".log.gz"
  }
  depends_on = [aws_sqs_queue_policy.alb_access_logs]
}

resource "aws_iam_role" "alb_log_collector" {
  name = "${var.project}-${var.environment}-alb-log-collector"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" },
    Condition = { StringEquals = {
      "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
      "aws:RequestTag/kubernetes-namespace"       = "observability"
      "aws:RequestTag/kubernetes-service-account" = "alb-log-collector"
    } }
  }] })
}

resource "aws_iam_role_policy" "alb_log_collector" {
  name = "read-alb-access-logs"
  role = aws_iam_role.alb_log_collector.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "ReadRawLogs", Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.alb_access_logs.arn}/${local.alb_log_objects}*" },
    { Sid = "ListLogPrefix", Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.alb_access_logs.arn, Condition = { StringLike = { "s3:prefix" = "${local.alb_log_objects}*" } } },
    { Sid = "ConsumeNotifications", Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.alb_access_logs.arn },
    { Sid = "Quarantine", Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.alb_access_logs_dead_letter.arn },
    # ELB discovery APIs have no resource-level authorization. No write APIs are granted.
    { Sid = "DiscoverTargetGroups", Effect = "Allow", Action = ["elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeTags"], Resource = "*", Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } } }
  ] })
}

output "alb_access_logs" {
  description = "Non-secret ALB anchor/collector inputs; enable only after storage and image publication."
  value = {
    bucket                = aws_s3_bucket.alb_access_logs.bucket, prefix = local.alb_log_prefix,
    object_prefix         = local.alb_log_objects, queue_url = aws_sqs_queue.alb_access_logs.id,
    dead_letter_queue_url = aws_sqs_queue.alb_access_logs_dead_letter.id,
    collector_role_arn    = aws_iam_role.alb_log_collector.arn
  }
}
