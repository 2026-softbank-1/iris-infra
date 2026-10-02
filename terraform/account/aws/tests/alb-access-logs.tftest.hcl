mock_provider "aws" {}
variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example/infra"
}
run "collector_deployment_boundary" {
  command = plan
  assert {
    condition     = length(aws_iam_policy.alb_access_logs.policy) <= 6144 && alltrue([for s in jsondecode(aws_iam_policy.alb_access_logs.policy).Statement : !contains(s.Action, "s3:DeleteObject") && !contains(s.Action, "s3:PutObject") && !contains(s.Action, "sqs:ReceiveMessage")])
    error_message = "CI manages only the dedicated log infrastructure, never reads or writes log bodies."
  }
  assert {
    condition = one([for s in jsondecode(aws_iam_policy.alb_access_logs.policy).Statement : s if s.Sid == "CollectorRole"]).Resource == "arn:aws:iam::123456789012:role/iris-dev-alb-log-collector" && alltrue([
      for s in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : !contains(s.Resource, "arn:aws:iam::123456789012:role/iris-dev-alb-log-collector") if s.Sid == "PassEksRoles"
    ]) && one([for s in jsondecode(aws_iam_policy.alb_access_logs.policy).Statement : s if s.Sid == "PassCollectorRole"]).Condition.StringEquals["iam:PassedToService"] == "pods.eks.amazonaws.com"
    error_message = "Collector role may be passed only to Pod Identity, never EC2."
  }
  assert {
    condition = one([for s in jsondecode(aws_iam_policy.alb_access_logs.policy).Statement : s if s.Sid == "ManageQueues"]).Resource == [
      "arn:aws:sqs:ap-northeast-2:123456789012:iris-dev-alb-access-logs",
      "arn:aws:sqs:ap-northeast-2:123456789012:iris-dev-alb-access-logs-dlq"
    ]
    error_message = "SQS deployment actions must be limited to the two new named queues."
  }
}
