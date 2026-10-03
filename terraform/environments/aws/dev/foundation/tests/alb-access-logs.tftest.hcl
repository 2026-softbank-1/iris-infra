mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock-build-role"
    }
  }
}
# Mock providers cannot serve the imported zone, and ACM validation options are
# computed by AWS; fixed values keep plan-time indexing deterministic.
override_resource {
  target = aws_route53_zone.main
  values = { zone_id = "Z0123456789ABCDEFGHIJ", name_servers = ["ns-1.awsdns-01.org"] }
}
override_resource {
  target = aws_acm_certificate.wildcard
  values = { domain_validation_options = [
    { domain_name = "*.likelion.uk", resource_record_name = "_x.likelion.uk.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." },
    { domain_name = "likelion.uk", resource_record_name = "_x.likelion.uk.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." }
  ] }
}

variables {
  aws_account_id = "123456789012"
}

run "alb_storage_and_delivery_boundary" {
  command = apply
  assert {
    condition     = aws_s3_bucket.alb_access_logs.bucket == "iris-dev-alb-access-logs-123456789012-ap-northeast-2" && !aws_s3_bucket.alb_access_logs.force_destroy && aws_s3_bucket_public_access_block.alb_access_logs.block_public_policy && one(one(aws_s3_bucket_server_side_encryption_configuration.alb_access_logs.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "Raw logs must be private SSE-S3 data in a protected separate bucket."
  }
  assert {
    condition     = one([for s in jsondecode(aws_s3_bucket_policy.alb_access_logs.policy).Statement : s if s.Sid == "ALBLogDelivery"]).Condition.StringEquals["aws:SourceAccount"] == "123456789012" && one(aws_s3_bucket_notification.alb_access_logs.queue).filter_prefix == "alb/workload/AWSLogs/123456789012/elasticloadbalancing/ap-northeast-2/" && one(aws_s3_bucket_notification.alb_access_logs.queue).filter_suffix == ".log.gz"
    error_message = "Deliver only the workload ALB log prefix, not Loki objects or test notifications."
  }
  assert {
    condition     = aws_sqs_queue.alb_access_logs.sqs_managed_sse_enabled && aws_sqs_queue.alb_access_logs_dead_letter.message_retention_seconds == 1209600 && jsondecode(aws_sqs_queue.alb_access_logs.redrive_policy).maxReceiveCount == 10 && one(aws_s3_bucket_lifecycle_configuration.alb_access_logs.rule).expiration[0].days == 7
    error_message = "Queue retries are bounded and raw logs expire after seven days."
  }
  assert {
    condition     = jsondecode(aws_iam_role.alb_log_collector.assume_role_policy).Statement[0].Condition.StringEquals["aws:RequestTag/kubernetes-service-account"] == "alb-log-collector" && alltrue([for s in jsondecode(aws_iam_role_policy.alb_log_collector.policy).Statement : !contains(s.Action, "s3:PutObject") && !contains(s.Action, "s3:DeleteObject")])
    error_message = "Only the management collector service account may consume read-only source logs."
  }
  assert {
    condition = jsonencode(one([for s in jsondecode(aws_s3_bucket_policy.alb_access_logs.policy).Statement : s if s.Sid == "ALBLogDeliveryRegionalAccount"])) == jsonencode({
      Sid    = "ALBLogDeliveryRegionalAccount", Effect = "Allow", Principal = { AWS = "arn:aws:iam::600734575887:root" },
      Action = "s3:PutObject", Resource = "${aws_s3_bucket.alb_access_logs.arn}/alb/workload/AWSLogs/123456789012/*"
    })
    error_message = "ap-northeast-2 delivers ALB logs as the regional ELB account: write-only, limited to this account's workload log prefix."
  }
}
