mock_provider "aws" {}
variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example/infra"
}
run "control_api_deployment_boundary" {
  command = plan
  assert {
    condition = length(aws_iam_policy.control_api.policy) <= 6144 && length(jsondecode(aws_iam_policy.control_api.policy).Statement) == 3 && alltrue([
      for s in jsondecode(aws_iam_policy.control_api.policy).Statement : !contains(s.Action, "iam:*") && !contains(s.Action, "iam:AttachRolePolicy")
    ])
    error_message = "CI may administer the Control API roles only with inline policies and without attaching managed policies."
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_policy.control_api.policy).Statement : s if s.Sid == "ControlApiRole"]).Resource == ["arn:aws:iam::123456789012:role/iris-dev-control-api", "arn:aws:iam::123456789012:role/iris-dev-onprem-ecr-pull"]
    error_message = "CI may administer only the existing Control API and on-prem pull roles; the legacy renewal role must not be created or deleted."
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_policy.control_api.policy).Statement : s if s.Sid == "ExistingOnpremRenewalPolicy"]).Resource == "arn:aws:iam::123456789012:role/iris-dev-onprem-ecr-svc-28" && jsonencode(one([for s in jsondecode(aws_iam_policy.control_api.policy).Statement : s if s.Sid == "ExistingOnpremRenewalPolicy"]).Action) == jsonencode(["iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy"])
    error_message = "CI may manage only inline policies on the manually owned renewal role, without role creation, deletion, trust changes or PassRole."
  }

  assert {
    condition     = one([for s in jsondecode(aws_iam_policy.control_api.policy).Statement : s if s.Sid == "PassControlApiRole"]).Resource == "arn:aws:iam::123456789012:role/iris-dev-control-api" && one([for s in jsondecode(aws_iam_policy.control_api.policy).Statement : s if s.Sid == "PassControlApiRole"]).Action == ["iam:PassRole"] && jsonencode(one([for s in jsondecode(aws_iam_policy.control_api.policy).Statement : s if s.Sid == "PassControlApiRole"]).Condition) == jsonencode({ StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } })
    error_message = "Only the Control API role may be passed, and only to EKS Pod Identity; the on-prem ECR pull role is never passed."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : !contains(s.Resource, "arn:aws:iam::123456789012:role/iris-dev-control-api") if s.Sid == "PassEksRoles"
    ]) && !contains(local.runtime_role_arns, "arn:aws:iam::123456789012:role/iris-dev-control-api")
    error_message = "The Control API role must stay outside the broad EKS runtime role inventory."
  }
  assert {
    condition     = aws_iam_role_policy_attachment.control_api.role == aws_iam_role.terraform_apply.name
    error_message = "The Control API deployment policy attaches to the CI apply role."
  }
}
