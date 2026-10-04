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
run "reuse_existing_onprem_identity" {
  command = apply
  assert {
    condition     = aws_iam_role_policy.onprem_ecr_renewer.role == "iris-dev-onprem-ecr-svc-28" && jsonencode(jsondecode(aws_iam_role_policy.onprem_ecr_renewer.policy).Statement) == jsonencode([{ Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.onprem_ecr_pull.arn }])
    error_message = "Reuse the existing issuer with only a new scoped AssumeRole inline policy; do not create a new role or replace its existing image-pull policy."
  }
  assert {
    condition = jsonencode(jsondecode(aws_iam_role.onprem_ecr_pull.assume_role_policy).Statement[1]) == jsonencode({
      Sid = "ExistingOnpremRenewal", Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { AWS = "arn:aws:iam::123456789012:role/iris-dev-onprem-ecr-svc-28" },
      Condition = { StringEquals = {
        "aws:PrincipalTag/eks-cluster-name"           = var.management_cluster_name,
        "aws:PrincipalTag/kubernetes-namespace"       = "iris-platform",
        "aws:PrincipalTag/kubernetes-service-account" = "iris-onprem-ecr-renewer-28"
      } }
    })
    error_message = "The additional pull-role trust must be separate and bound to the existing renewal Pod Identity tags."
  }
}
