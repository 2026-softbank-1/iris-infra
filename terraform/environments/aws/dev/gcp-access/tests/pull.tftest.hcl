mock_provider "aws" {}
variables {
  aws_account_id         = "123456789012"
  gcp_service_account_id = "123456789012345678901"
}
run "bound_identity_read_only" {
  command = plan
  assert {
    condition     = length(aws_iam_role.publisher) == 0 && length(aws_iam_role_policy.publisher) == 0
    error_message = "Image publisher must be opt-in and operator managed."
  }
  override_resource {
    target          = aws_ecr_repository.credentials
    override_during = plan
    values          = { arn = "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/gcp-ecr-credentials" }
  }
  assert {
    condition     = jsondecode(aws_iam_role.pull.assume_role_policy).Statement[0].Condition.StringEquals["accounts.google.com:sub"] == var.gcp_service_account_id && jsondecode(aws_iam_role.pull.assume_role_policy).Statement[0].Condition.StringEquals["accounts.google.com:aud"] == var.gcp_service_account_id && jsondecode(aws_iam_role.pull.assume_role_policy).Statement[0].Condition.StringEquals["accounts.google.com:oaud"] == var.audience
    error_message = "Bind the exact Google identity and intended audience."
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.pull.policy).Statement[1].Action) == toset(["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]) && !aws_ecr_repository.credentials.force_delete
    error_message = "Pull identity must not publish or destroy images."
  }
}
run "publisher_exact_main_one_repository" {
  command = plan
  variables {
    enable_github_publisher    = true
    github_oidc_subject_prefix = "repo:2026-softbank-1@987654321/iris-infra@123456789"
  }
  override_resource {
    target          = aws_ecr_repository.credentials
    override_during = plan
    values          = { arn = "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/gcp-ecr-credentials" }
  }
  assert {
    condition     = jsondecode(aws_iam_role.publisher[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:2026-softbank-1@987654321/iris-infra@123456789:ref:refs/heads/main" && jsondecode(aws_iam_role.publisher[0].assume_role_policy).Statement[0].Principal.Federated == "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    error_message = "Reuse the existing provider and exact customized main subject."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.publisher[0].policy).Statement[1].Resource == aws_ecr_repository.credentials.arn && !contains(jsondecode(aws_iam_role_policy.publisher[0].policy).Statement[1].Action, "ecr:BatchDeleteImage") && contains(jsondecode(aws_iam_role_policy.publisher[0].policy).Statement[1].Action, "ecr:PutImage") && !contains(jsondecode(aws_iam_role_policy.publisher[0].policy).Statement[1].Action, "iam:PassRole")
    error_message = "Publisher may upload only this image repository, without delete or IAM grants."
  }
}
