mock_provider "aws" {
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/mock" } }
}
variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example/infra"
}
run "runtime_boundaries_and_session" {
  command = apply
  assert {
    condition     = aws_iam_role.terraform_apply.max_session_duration == 7200 && alltrue([for policy in [aws_iam_policy.eks_deployment, aws_iam_policy.runtime_iam, aws_iam_policy.runtime_compute, aws_iam_policy.bridge_launch] : length(policy.policy) <= 6144])
    error_message = "Runtime policies must fit AWS size limits: eks=${length(aws_iam_policy.eks_deployment.policy)}, iam=${length(aws_iam_policy.runtime_iam.policy)}, compute=${length(aws_iam_policy.runtime_compute.policy)}, bridge=${length(aws_iam_policy.bridge_launch.policy)}; session must be 7200 seconds."
  }
  assert {
    condition     = !contains(local.runtime_role_arns, aws_iam_role.terraform_apply.arn) && alltrue([for statement in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : !contains(statement.Action, "iam:*")])
    error_message = "CI cannot administer its own role and runtime IAM actions must be explicit."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.runtime_iam.policy).Statement[1].Condition.ArnEquals["iam:PolicyARN"] == local.attach_policy_arns && !strcontains(aws_iam_policy.runtime_iam.policy, "token.actions.githubusercontent.com")
    error_message = "Only reviewed policies may attach; GitHub OIDC trust remains out of scope."
  }
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_policy.runtime_compute.policy).Statement : statement.Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2"])
    error_message = "Compute authorization must remain regional."
  }
  assert {
    condition = length([for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement : statement if statement.Sid == "RunTaggedBridgeVolume"]) == 1 && alltrue([
      for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement :
      statement.Effect == "Allow" && statement.Action == ["ec2:RunInstances"] && statement.Resource == "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:volume/*" && jsonencode(statement.Condition) == jsonencode({
        StringEquals = {
          "aws:RequestedRegion"        = var.aws_region
          "aws:RequestTag/Project"     = var.project
          "aws:RequestTag/Environment" = var.environment
          "aws:RequestTag/ManagedBy"   = "Terraform"
          "aws:RequestTag/Component"   = ["eks", "access"]
        }
      }) if statement.Sid == "RunTaggedBridgeVolume"
    ])
    error_message = "Bridge volume RunInstances authorization must retain the exact account/region ARN and all four owner request tags; do not relax IAM to allow untagged volumes."
  }
}
