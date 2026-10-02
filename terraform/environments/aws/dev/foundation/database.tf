# Platform metadata DB shared by the Control API, Build Worker and Deploy Worker (one jobs queue).
# Lives in foundation so it survives cluster teardown. Single-AZ by decision; set multi_az for production.
locals {
  db_name = "${var.project}-${var.environment}-platform"
}
resource "aws_db_subnet_group" "platform" {
  name       = local.db_name
  subnet_ids = [for slot in keys(local.network_slots) : aws_subnet.private["management-${slot}"].id]
}
resource "aws_security_group" "platform_db" {
  name        = "${local.db_name}-db"
  description = "Platform PostgreSQL; accepts only management cluster nodes"
  vpc_id      = aws_vpc.shared.id
  tags        = { Name = "${local.db_name}-db" }
}
# Every management node carries management_api_source, and VPC CNI pods use the node ENI groups.
resource "aws_vpc_security_group_ingress_rule" "platform_db_from_management" {
  security_group_id            = aws_security_group.platform_db.id
  referenced_security_group_id = aws_security_group.management_api_source.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_db_parameter_group" "platform" {
  name   = local.db_name
  family = "postgres17"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}
ephemeral "random_password" "platform_db" {
  length  = 32
  special = false
}
# Operators read this to build DATABASE_URL for the platform Secrets (runbook deploy-platform).
resource "aws_secretsmanager_secret" "platform_db" {
  name                    = "${local.db_name}-db"
  description             = "Platform RDS credentials (username/password/host/port/dbname)"
  recovery_window_in_days = 7
}
resource "aws_secretsmanager_secret_version" "platform_db" {
  secret_id = aws_secretsmanager_secret.platform_db.id
  secret_string_wo = jsonencode({
    username = "iris"
    password = ephemeral.random_password.platform_db.result
    host     = aws_db_instance.platform.address
    port     = aws_db_instance.platform.port
    dbname   = "iris"
  })
  secret_string_wo_version = var.platform_db_password_version
}
resource "aws_db_instance" "platform" {
  identifier     = local.db_name
  engine         = "postgres"
  engine_version = var.platform_db_engine_version
  instance_class = var.platform_db_instance_class
  db_name        = "iris"
  username       = "iris"
  # Write-only: the password never enters plan/state. Bump platform_db_password_version to rotate
  # (RDS-managed passwords rotate every 7 days and would break the DATABASE_URL Secrets in EKS).
  password_wo         = ephemeral.random_password.platform_db.result
  password_wo_version = var.platform_db_password_version

  allocated_storage     = 20
  max_allocated_storage = 50
  storage_type          = "gp3"
  storage_encrypted     = true

  multi_az               = false
  publicly_accessible    = false
  db_subnet_group_name   = aws_db_subnet_group.platform.name
  vpc_security_group_ids = [aws_security_group.platform_db.id]
  parameter_group_name   = aws_db_parameter_group.platform.name

  backup_retention_period    = 7
  copy_tags_to_snapshot      = true
  auto_minor_version_upgrade = true
  # Avoid silent paid Extended Support once the major version leaves standard support.
  engine_lifecycle_support  = "open-source-rds-extended-support-disabled"
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.db_name}-final"
  apply_immediately         = false
}
output "platform_db_endpoint" { value = aws_db_instance.platform.address }
output "platform_db_port" { value = aws_db_instance.platform.port }
output "platform_db_name" { value = aws_db_instance.platform.db_name }
output "platform_db_secret_arn" {
  description = "Secrets Manager secret with the DB credentials; build DATABASE_URL from it."
  value       = aws_secretsmanager_secret.platform_db.arn
}
