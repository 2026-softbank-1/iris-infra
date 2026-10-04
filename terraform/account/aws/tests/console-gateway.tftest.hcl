mock_provider "aws" {}
variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example/infra"
}
run "console_gateway_deployment_boundary" {
  command = plan
  assert {
    condition = length(aws_iam_policy.console_gateway.policy) <= 6144 && length(jsondecode(aws_iam_policy.console_gateway.policy).Statement) == 2 && alltrue([
      for s in jsondecode(aws_iam_policy.console_gateway.policy).Statement : !contains(s.Action, "iam:*") && !contains(s.Action, "iam:AttachRolePolicy") && !contains(s.Action, "iam:PutRolePolicy")
    ])
    error_message = "CI may administer the Console Gateway role only without attaching managed policies or adding permission policies to it."
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_policy.console_gateway.policy).Statement : s if s.Sid == "ConsoleGatewayRole"]).Resource == "arn:aws:iam::123456789012:role/iris-dev-console-gateway"
    error_message = "CI may administer only the Console Gateway role."
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_policy.console_gateway.policy).Statement : s if s.Sid == "PassConsoleGatewayRole"]).Resource == "arn:aws:iam::123456789012:role/iris-dev-console-gateway" && one([for s in jsondecode(aws_iam_policy.console_gateway.policy).Statement : s if s.Sid == "PassConsoleGatewayRole"]).Action == ["iam:PassRole"] && jsonencode(one([for s in jsondecode(aws_iam_policy.console_gateway.policy).Statement : s if s.Sid == "PassConsoleGatewayRole"]).Condition) == jsonencode({ StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } })
    error_message = "Only the Console Gateway role may be passed, and only to EKS Pod Identity."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : !contains(s.Resource, "arn:aws:iam::123456789012:role/iris-dev-console-gateway") if s.Sid == "PassEksRoles"
    ]) && !contains(local.runtime_role_arns, "arn:aws:iam::123456789012:role/iris-dev-console-gateway")
    error_message = "The Console Gateway role must stay outside the broad EKS runtime role inventory."
  }
  assert {
    condition     = aws_iam_role_policy_attachment.console_gateway.role == aws_iam_role.terraform_apply.name
    error_message = "The Console Gateway deployment policy attaches to the CI apply role."
  }
}
