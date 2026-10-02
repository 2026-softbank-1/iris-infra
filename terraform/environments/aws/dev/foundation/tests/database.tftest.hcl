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
run "private_single_az_platform_db" {
  command = apply
  assert {
    condition     = !aws_db_instance.platform.publicly_accessible && !aws_db_instance.platform.multi_az && aws_db_instance.platform.storage_encrypted && aws_db_instance.platform.deletion_protection && !aws_db_instance.platform.skip_final_snapshot && aws_db_instance.platform.password_wo_version == aws_secretsmanager_secret_version.platform_db.secret_string_wo_version && aws_db_instance.platform.backup_retention_period == 7
    error_message = "Platform DB must be private, single-AZ, encrypted, protected, and rotate its write-only password together with the secret."
  }
  assert {
    condition     = toset(aws_db_subnet_group.platform.subnet_ids) == toset([aws_subnet.private["management-0"].id, aws_subnet.private["management-1"].id]) && aws_vpc_security_group_ingress_rule.platform_db_from_management.referenced_security_group_id == aws_security_group.management_api_source.id && aws_vpc_security_group_ingress_rule.platform_db_from_management.from_port == 5432 && aws_vpc_security_group_ingress_rule.platform_db_from_management.cidr_ipv4 == null
    error_message = "Only management nodes reach the DB, from the management private subnets."
  }
}
