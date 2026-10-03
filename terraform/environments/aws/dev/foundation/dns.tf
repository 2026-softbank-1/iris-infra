# Service domain. The hosted zone already existed (created outside Terraform) and is
# adopted here; records Terraform does not declare (apex/app/www → CloudFront) stay untouched.
import {
  to = aws_route53_zone.main
  id = "Z05155092SZ0JAY5VNXXR"
}
resource "aws_route53_zone" "main" {
  name    = var.domain_name
  comment = ""
  lifecycle { prevent_destroy = true }
}

# Shared ALB certificate. LBC discovers it from the Ingress host, so nothing references the ARN.
resource "aws_acm_certificate" "wildcard" {
  domain_name               = "*.${var.domain_name}"
  subject_alternative_names = [var.domain_name]
  validation_method         = "DNS"
  lifecycle { create_before_destroy = true }
}
# *.domain and the apex share one validation record; names are unknown until the cert exists.
locals { acm_validation = tolist(aws_acm_certificate.wildcard.domain_validation_options)[0] }
resource "aws_route53_record" "acm_validation" {
  zone_id = aws_route53_zone.main.zone_id
  name    = local.acm_validation.resource_record_name
  type    = local.acm_validation.resource_record_type
  records = [local.acm_validation.resource_record_value]
  ttl     = 300
}
# On-prem user services: *.internal reaches on-prem via the management ALB → nginx → Tailscale.
# *.domain does not cover a second label, so it needs its own certificate (no SAN, one validation record).
resource "aws_acm_certificate" "internal" {
  domain_name       = "*.internal.${var.domain_name}"
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}
locals { internal_acm_validation = tolist(aws_acm_certificate.internal.domain_validation_options)[0] }
resource "aws_route53_record" "internal_acm_validation" {
  zone_id = aws_route53_zone.main.zone_id
  name    = local.internal_acm_validation.resource_record_name
  type    = local.internal_acm_validation.resource_record_type
  records = [local.internal_acm_validation.resource_record_value]
  ttl     = 300
}
output "dns_zone_name_servers" {
  description = "Route53 위임 NS. 등록기관 NS가 이 값이어야 Route53 레코드가 공개 DNS에 반영됩니다."
  value       = aws_route53_zone.main.name_servers
}
output "acm_certificate_arn" { value = aws_acm_certificate.wildcard.arn }
output "acm_validation_records" {
  description = "Route53이 권한 DNS가 아닐 때 현재 DNS(Cloudflare)에 같은 CNAME을 등록합니다."
  value       = { name = aws_route53_record.acm_validation.name, type = aws_route53_record.acm_validation.type, value = one(aws_route53_record.acm_validation.records) }
}
output "internal_acm_certificate_arn" { value = aws_acm_certificate.internal.arn }
output "internal_acm_validation_records" {
  description = "Cloudflare(권한 DNS)에 같은 CNAME 을 등록해야 인증서가 발급됩니다."
  value       = { name = aws_route53_record.internal_acm_validation.name, type = aws_route53_record.internal_acm_validation.type, value = one(aws_route53_record.internal_acm_validation.records) }
}
