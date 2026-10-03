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

run "platform_images_and_retention" {
  command = apply

  assert {
    condition = toset([for repository in aws_ecr_repository.platform : repository.name]) == toset([
      "iris/was", "iris/code-analyzer-agent", "iris/error-check-agent", "iris/alb-log-collector",
    ])
    error_message = "Create the platform and collector repositories, without dev or web."
  }

  assert {
    condition = alltrue([
      for repository in aws_ecr_repository.platform :
      repository.image_tag_mutability == "IMMUTABLE" && !repository.force_delete &&
      one(repository.encryption_configuration).encryption_type == "AES256" &&
      one(repository.image_scanning_configuration).scan_on_push
    ])
    error_message = "Platform releases must be immutable, encrypted, scanned on push and protected from force deletion."
  }

  assert {
    condition = alltrue([
      for policy in aws_ecr_lifecycle_policy.platform :
      length(jsondecode(policy.policy).rules) == 1 &&
      jsondecode(policy.policy).rules[0].selection.tagStatus == "untagged" &&
      jsondecode(policy.policy).rules[0].selection.countType == "sinceImagePushed" &&
      jsondecode(policy.policy).rules[0].selection.countUnit == "days" &&
      jsondecode(policy.policy).rules[0].selection.countNumber == 30 &&
      jsondecode(policy.policy).rules[0].action.type == "expire"
    ])
    error_message = "Expire only untagged images after 30 days; tagged deployments and rollbacks must remain available."
  }

  assert {
    condition = (
      one([for statement in jsondecode(aws_iam_role_policy.codebuild.policy).Statement : statement if statement.Sid == "PushServiceImages"]).Resource == "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/services/*" &&
      one([for statement in jsondecode(aws_iam_role_policy.build_worker.policy).Statement : statement if statement.Sid == "ManageServiceRepositories"]).Resource == "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/services/*"
    )
    error_message = "Existing CodeBuild and Build Worker permissions must stay on user service repositories."
  }
}
