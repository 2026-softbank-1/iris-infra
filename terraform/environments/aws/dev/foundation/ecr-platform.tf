# Platform images are separate from Build Worker's dynamic iris/services/* repositories.
# Both foundation and account read the same non-secret repository inventory.
locals {
  platform_ecr_suffixes = jsondecode(file("${path.module}/../../../../config/platform-ecr-repositories.json"))
  platform_ecr_names = {
    for suffix in local.platform_ecr_suffixes : suffix => "${var.project}/${suffix}"
  }
}

resource "aws_ecr_repository" "platform" {
  for_each = local.platform_ecr_names

  name                 = each.value
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  lifecycle {
    prevent_destroy = true

    precondition {
      condition = (
        length(each.value) <= 256 &&
        can(regex("^[a-z][a-z0-9._-]*/[a-z0-9]+([._-][a-z0-9]+)*$", each.value))
      )
      error_message = "Platform repository names must be project/suffix; suffixes cannot contain slashes."
    }
  }
}

resource "aws_ecr_lifecycle_policy" "platform" {
  for_each = aws_ecr_repository.platform

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 30 days; keep tagged releases"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

output "platform_ecr_repository_urls" {
  description = "Platform image repository URLs, keyed by service suffix."
  value       = { for suffix, repository in aws_ecr_repository.platform : suffix => repository.repository_url }
}

output "platform_ecr_repository_arns" {
  description = "Platform image repository ARNs, keyed by service suffix."
  value       = { for suffix, repository in aws_ecr_repository.platform : suffix => repository.arn }
}
