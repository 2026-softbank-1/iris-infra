mock_provider "aws" {
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::123456789012:policy/iris-dev-foundation-network" }
  }
}

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
      alltrue([
        for resource in try(tolist(statement.Resource), [tostring(statement.Resource)]) :
        startswith(resource, "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2")
      ])
    ])
    error_message = "The state policy must be restricted to its state bucket."
  }

  assert {
    condition = toset(flatten([
      for statement in jsondecode(aws_iam_role_policy.terraform_apply.policy).Statement :
      statement.Resource if contains(statement.Action, "s3:GetObject") && contains(statement.Action, "s3:PutObject") && !contains(statement.Action, "s3:DeleteObject")
      ])) == toset([
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/bootstrap/aws/terraform.tfstate",
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/aws/dev/foundation/terraform.tfstate",
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/aws/dev/management/terraform.tfstate",
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/aws/dev/workload/terraform.tfstate",
    ])
    error_message = "All four deployment states need Get/Put; account state must remain administrator-only."
  }

  assert {
    condition = toset(flatten([
      for statement in jsondecode(aws_iam_role_policy.terraform_apply.policy).Statement :
      statement.Resource if contains(statement.Action, "s3:DeleteObject")
      ])) == toset([
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/bootstrap/aws/terraform.tfstate.tflock",
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/aws/dev/foundation/terraform.tfstate.tflock",
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/aws/dev/management/terraform.tfstate.tflock",
      "arn:aws:s3:::iris-tfstate-123456789012-ap-northeast-2/aws/dev/workload/terraform.tfstate.tflock",
    ])
    error_message = "Only deployment lock files may be deleted in the state bucket."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.terraform_apply.policy).Statement :
      !contains(statement.Action, "s3:DeleteBucket") && !contains(statement.Action, "s3:DeleteObjectVersion") && !contains(statement.Action, "s3:*")
    ])
    error_message = "CI must not receive bucket deletion or unrestricted S3 permissions."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.foundation.policy).Statement :
      alltrue([
        for action in statement.Action :
        !contains(["*", "iam:*", "s3:*", "logs:*", "codebuild:*"], action)
        ]) && (
        statement.Resource != "*" || (
          statement.Action == ["logs:DescribeLogGroups"] &&
          try(statement.Condition.StringEquals["aws:RequestedRegion"], "") == "ap-northeast-2"
        )
      )
    ])
    error_message = "Foundation must use scoped actions/resources, except regional log-group discovery."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.foundation.policy).Statement :
      alltrue([
        for resource in try(tolist(statement.Resource), [tostring(statement.Resource)]) :
        contains([
          "arn:aws:iam::123456789012:role/iris-dev-build-codebuild",
          "arn:aws:iam::123456789012:role/iris-dev-build-worker",
        ], resource)
      ]) if anytrue([for action in statement.Action : startswith(action, "iam:")])
    ])
    error_message = "CI may manage only the two build roles, never its own role or OIDC provider."
  }

  assert {
    condition = (
      length([for statement in jsondecode(aws_iam_role_policy.foundation.policy).Statement : statement if contains(statement.Action, "iam:PassRole")]) == 1 &&
      alltrue([
        for statement in jsondecode(aws_iam_role_policy.foundation.policy).Statement :
        statement.Resource == "arn:aws:iam::123456789012:role/iris-dev-build-codebuild" &&
        statement.Condition.StringEquals["iam:PassedToService"] == "codebuild.amazonaws.com"
        if contains(statement.Action, "iam:PassRole")
      ])
    )
    error_message = "PassRole must be limited to the CodeBuild role and CodeBuild service."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.foundation.policy).Statement :
      alltrue([
        for resource in try(tolist(statement.Resource), [tostring(statement.Resource)]) :
        contains([
          "arn:aws:s3:::iris-dev-build-artifacts-123456789012-ap-northeast-2",
          "arn:aws:s3:::iris-dev-build-artifacts-123456789012-ap-northeast-2/*",
        ], resource)
      ]) if anytrue([for action in statement.Action : startswith(action, "s3:")])
    ])
    error_message = "Foundation bucket and object deletion must never reach the state bucket."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.foundation.policy).Statement :
      statement.Resource == "arn:aws:codebuild:ap-northeast-2:123456789012:project/iris-dev-build" &&
      !contains(statement.Action, "codebuild:StartBuild")
      if anytrue([for action in statement.Action : startswith(action, "codebuild:")])
    ])
    error_message = "CI manages the one CodeBuild project; starting user builds belongs to the Build Worker."
  }
}
