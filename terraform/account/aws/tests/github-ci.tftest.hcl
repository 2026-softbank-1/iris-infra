mock_provider "aws" {}

variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example@123/infra@456"
}

run "restrict_github_deployment" {
  # Apply against the mock provider so computed OIDC ARNs are known; no AWS API calls occur.
  command = apply

  assert {
    condition = (
      length(jsondecode(aws_iam_role.terraform_apply.assume_role_policy).Statement[0].Condition.StringEquals) == 2 &&
      jsondecode(aws_iam_role.terraform_apply.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      jsondecode(aws_iam_role.terraform_apply.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example@123/infra@456:ref:refs/heads/main"
    )
    error_message = "Only this repository's main branch and the AWS STS audience may assume the role."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.terraform_apply.policy).Statement :
      startswith(statement.Resource, "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2")
    ])
    error_message = "The CI role must be restricted to its state bucket."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.terraform_apply.policy).Statement :
      !contains(statement.Action, "s3:DeleteObject") || statement.Resource == "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/bootstrap/aws/terraform.tfstate.tflock"
    ])
    error_message = "Only the lock file may be deleted; deleting the state must not be allowed."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.terraform_apply.policy).Statement :
      !contains(statement.Action, "s3:DeleteBucket") && !contains(statement.Action, "s3:*")
    ])
    error_message = "CI must not receive bucket deletion or unrestricted S3 permissions."
  }
}
