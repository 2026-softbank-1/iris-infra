mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
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
override_resource {
  target = aws_acm_certificate.internal
  values = { domain_validation_options = [
    { domain_name = "*.internal.likelion.uk", resource_record_name = "_z.internal.likelion.uk.", resource_record_type = "CNAME", resource_record_value = "_w.acm-validations.aws." }
  ] }
}
variables { aws_account_id = "123456789012" }
override_resource {
  target = aws_iam_role.onprem_ecr_renewer
  values = { arn = "arn:aws:iam::123456789012:role/iris-dev-onprem-ecr-renewer" }
}
override_resource {
  target = aws_iam_role.onprem_ecr_renewal_pull
  values = { arn = "arn:aws:iam::123456789012:role/iris-dev-onprem-ecr-renewal-pull" }
}
run "onprem_namespace_credentials" {
  command = apply
  assert {
    condition = aws_iam_role.onprem_ecr_renewer.name == "iris-dev-onprem-ecr-renewer" && jsonencode(jsondecode(aws_iam_role.onprem_ecr_renewer.assume_role_policy).Statement[0].Condition) == jsonencode({ StringEquals = {
      "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name,
      "aws:RequestTag/kubernetes-namespace"       = "iris-platform",
      "aws:RequestTag/kubernetes-service-account" = "iris-onprem-ecr-renewer"
    } }) && jsondecode(aws_iam_role.onprem_ecr_renewer.assume_role_policy).Statement[0].Principal.Service == "pods.eks.amazonaws.com"
    error_message = "Issuer trust must be bound to the management cluster and the exact reconciler identity."
  }
  assert {
    condition     = jsonencode(jsondecode(aws_iam_role_policy.onprem_ecr_renewer.policy).Statement) == jsonencode([{ Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.onprem_ecr_renewal_pull.arn }])
    error_message = "Issuer must have only permission to assume the dedicated pull role."
  }
  assert {
    condition     = jsondecode(aws_iam_role.onprem_ecr_renewal_pull.assume_role_policy).Statement[0].Principal.AWS == aws_iam_role.onprem_ecr_renewer.arn && jsonencode(jsondecode(aws_iam_role.onprem_ecr_renewal_pull.assume_role_policy).Statement[0].Action) == jsonencode(["sts:AssumeRole", "sts:TagSession"])
    error_message = "Only the issuer may assume the pull role, including transitive Pod Identity tags."
  }
  assert {
    condition = jsonencode(jsondecode(aws_iam_role_policy.onprem_ecr_renewal_pull.policy).Statement) == jsonencode([
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:ap-northeast-2:123456789012:repository/iris/services/*" }
    ])
    error_message = "Pull role grants only token issuance and user service image downloads, never platform images or push."
  }
}
