mock_provider "aws" {
  # The account module also attaches the foundation network managed policy.
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::123456789012:policy/iris-dev-foundation-network" }
  }
}

variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example@123/infra@456"
}

run "publishers_are_opt_in" {
  command = plan

  assert {
    condition     = length(aws_iam_role.ecr_publisher) == 0
    error_message = "No service publisher may be trusted until its actual GitHub subject is configured."
  }
}

run "restrict_platform_management_and_publishing" {
  command = apply

  variables {
    github_ecr_publishers = {
      was = {
        oidc_subject_prefix = "repo:example@123/iris-was@101"
        repository_names    = ["iris/was"]
      }
      code-analyzer-agent = {
        oidc_subject_prefix = "repo:example@123/iris-code-analyzer-agent@102"
        repository_names    = ["iris/code-analyzer-agent"]
      }
      error-check-agent = {
        oidc_subject_prefix = "repo:example/iris-error-check-agent"
        repository_names    = ["iris/error-check-agent"]
      }
    }
  }

  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.platform_ecr.policy).Statement) == 1 &&
      toset(jsondecode(aws_iam_role_policy.platform_ecr.policy).Statement[0].Resource) == toset([
        "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/was",
        "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/code-analyzer-agent",
        "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/error-check-agent",
      ]) &&
      alltrue([for action in jsondecode(aws_iam_role_policy.platform_ecr.policy).Statement[0].Action : startswith(action, "ecr:") && action != "ecr:*"]) &&
      !contains(jsondecode(aws_iam_role_policy.platform_ecr.policy).Statement[0].Action, "ecr:PutImage")
    )
    error_message = "Terraform ECR management must cover exactly three platform repositories and must not publish images."
  }

  assert {
    condition = (
      length(aws_iam_role_policy.terraform_apply.policy) +
      length(aws_iam_role_policy.foundation.policy) +
      length(aws_iam_role_policy.platform_ecr.policy) <= 10240
    )
    error_message = "The Terraform CI role's combined inline policies must fit the IAM 10,240-character role quota."
  }

  assert {
    condition = alltrue([
      for key, role in aws_iam_role.ecr_publisher :
      length(jsondecode(role.assume_role_policy).Statement) == 1 &&
      jsondecode(role.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity" &&
      length(jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals) == 2 &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "${var.github_ecr_publishers[key].oidc_subject_prefix}:ref:refs/heads/main"
    ])
    error_message = "Only each configured repository's main branch and STS audience may assume its publisher role."
  }

  assert {
    condition = alltrue([
      for key, policy in aws_iam_role_policy.ecr_publisher :
      length(jsondecode(policy.policy).Statement) == 2 &&
      jsondecode(policy.policy).Statement[0].Action == ["ecr:GetAuthorizationToken"] &&
      jsondecode(policy.policy).Statement[0].Resource == "*" &&
      jsondecode(policy.policy).Statement[0].Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      toset(jsondecode(policy.policy).Statement[1].Resource) == toset(["arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/${key}"]) &&
      toset(jsondecode(policy.policy).Statement[1].Action) == toset([
        "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
      ])
    ])
    error_message = "Each publisher may only authenticate and push its own platform image, without state, IAM or service-repository access."
  }
}

run "reject_user_service_repository" {
  command = plan
  variables {
    github_ecr_publishers = {
      invalid = {
        oidc_subject_prefix = "repo:example/iris-was"
        repository_names    = ["iris/services/demo"]
      }
    }
  }
  expect_failures = [var.github_ecr_publishers]
}

run "reject_ref_inside_subject_prefix" {
  command = plan
  variables {
    github_ecr_publishers = {
      invalid = {
        oidc_subject_prefix = "repo:example/iris-was:pull_request"
        repository_names    = ["iris/was"]
      }
    }
  }
  expect_failures = [var.github_ecr_publishers]
}
