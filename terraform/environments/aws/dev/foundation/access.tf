data "aws_ssm_parameter" "bridge_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
locals {
  access_name = "${var.project}-${var.environment}-ssm-bridge"
  access_tags = { Project = var.project, Environment = var.environment, ManagedBy = "Terraform", Component = "access" }
}
resource "aws_security_group" "ssm_bridge" {
  name        = local.access_name
  description = "SSM bridge without inbound access or SSH"
  vpc_id      = aws_vpc.shared.id
  tags        = local.access_tags
}
resource "aws_vpc_security_group_egress_rule" "bridge_https" {
  security_group_id = aws_security_group.ssm_bridge.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  tags              = local.access_tags
}
resource "aws_vpc_security_group_egress_rule" "bridge_dns" {
  for_each          = toset(["tcp", "udp"])
  security_group_id = aws_security_group.ssm_bridge.id
  cidr_ipv4         = "${cidrhost(var.vpc_cidr, 2)}/32"
  ip_protocol       = each.value
  from_port         = 53
  to_port           = 53
  tags              = local.access_tags
}
resource "aws_iam_role" "ssm_bridge" {
  name               = local.access_name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }] })
  tags               = local.access_tags
}
resource "aws_iam_role_policy_attachment" "ssm_bridge" {
  role       = aws_iam_role.ssm_bridge.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "ssm_bridge" {
  name = local.access_name
  role = aws_iam_role.ssm_bridge.name
  tags = local.access_tags
}
resource "aws_instance" "ssm_bridge" {
  ami                         = data.aws_ssm_parameter.bridge_ami.value
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.private["management-0"].id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.ssm_bridge.id]
  iam_instance_profile        = aws_iam_instance_profile.ssm_bridge.name
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_size           = 8
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
    tags                  = local.access_tags
  }
  credit_specification { cpu_credits = "standard" }
  user_data  = <<-SH
    #!/bin/bash
    set -euo pipefail
    systemctl enable --now amazon-ssm-agent
  SH
  tags       = merge(local.access_tags, { Name = local.access_name })
  depends_on = [aws_route.private_internet, aws_iam_role_policy_attachment.ssm_bridge]
}
output "ssm_bridge_instance_id" { value = aws_instance.ssm_bridge.id }
output "ssm_bridge_security_group_id" { value = aws_security_group.ssm_bridge.id }
