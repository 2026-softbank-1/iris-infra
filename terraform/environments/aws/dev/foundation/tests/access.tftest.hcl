mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
}
variables { aws_account_id = "123456789012" }
run "private_bridge_and_argocd" {
  command = apply
  assert {
    condition     = !aws_instance.ssm_bridge.associate_public_ip_address && aws_instance.ssm_bridge.instance_type == "t3.micro" && aws_instance.ssm_bridge.subnet_id == aws_subnet.private["management-0"].id && length(aws_security_group.ssm_bridge.ingress) == 0 && aws_instance.ssm_bridge.credit_specification[0].cpu_credits == "standard"
    error_message = "Bridge must be private, no inbound/SSH and no surplus CPU charges."
  }
  assert {
    condition     = aws_iam_role_policy_attachment.ssm_bridge.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" && length(aws_iam_role.argocd_deploy) == 2 && jsondecode(aws_iam_role_policy.argocd_management.policy).Statement[0].Action == ["sts:AssumeRole", "sts:TagSession"]
    error_message = "Bridge gets no Kubernetes admin role; GitOps roles only assume named cluster identities."
  }
}
