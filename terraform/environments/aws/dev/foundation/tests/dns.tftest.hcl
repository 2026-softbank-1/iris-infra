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
variables { aws_account_id = "123456789012" }
run "wildcard_certificate" {
  command = apply
  assert {
    condition     = aws_acm_certificate.wildcard.domain_name == "*.likelion.uk" && aws_acm_certificate.wildcard.subject_alternative_names == toset(["likelion.uk"]) && aws_acm_certificate.wildcard.validation_method == "DNS"
    error_message = "One DNS-validated wildcard + apex certificate serves the shared ALB."
  }
  assert {
    condition     = aws_route53_record.acm_validation.name == "_x.likelion.uk." && aws_route53_record.acm_validation.zone_id == aws_route53_zone.main.zone_id
    error_message = "Wildcard and apex share a single validation record in the service zone."
  }
}
