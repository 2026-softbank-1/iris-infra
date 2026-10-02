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
run "private_bridge_and_argocd" {
  command = apply
  assert {
    condition     = !aws_instance.ssm_bridge.associate_public_ip_address && aws_instance.ssm_bridge.instance_type == "t3.micro" && aws_instance.ssm_bridge.subnet_id == aws_subnet.private["management-0"].id && length(aws_security_group.ssm_bridge.ingress) == 0 && aws_instance.ssm_bridge.credit_specification[0].cpu_credits == "standard"
    error_message = "Bridge must be private, no inbound/SSH and no surplus CPU charges."
  }
  assert {
    condition     = aws_instance.ssm_bridge.volume_tags == tomap(local.access_tags) && aws_instance.ssm_bridge.volume_tags["Component"] == "access" && aws_instance.ssm_bridge.root_block_device[0].encrypted && aws_instance.ssm_bridge.root_block_device[0].volume_type == "gp3" && aws_instance.ssm_bridge.root_block_device[0].volume_size == 8 && aws_instance.ssm_bridge.root_block_device[0].delete_on_termination
    error_message = "Bridge must send all owner tags in the launch request and retain its encrypted 8Gi gp3 root volume. Post-launch root_block_device tags cannot satisfy RunInstances RequestTag conditions."
  }
  assert {
    condition     = aws_iam_role_policy_attachment.ssm_bridge.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" && length(aws_iam_role.argocd_deploy) == 2 && jsondecode(aws_iam_role_policy.argocd_management.policy).Statement[0].Action == ["sts:AssumeRole", "sts:TagSession"]
    error_message = "Bridge gets no Kubernetes admin role; GitOps roles only assume named cluster identities."
  }
}

run "deploy_worker_boundary" {
  command = apply
  assert {
    condition = aws_iam_role.deploy_worker.name == "${var.project}-${var.environment}-deploy-worker" && jsonencode(jsondecode(aws_iam_role.deploy_worker.assume_role_policy).Statement) == jsonencode([{
      Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" },
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name,
        "aws:RequestTag/kubernetes-namespace"       = "iris-platform",
        "aws:RequestTag/kubernetes-service-account" = "deploy-worker"
      } }
    }])
    error_message = "Only management iris-platform/deploy-worker may assume the Deploy Worker role."
  }
  assert {
    condition = jsonencode(jsondecode(aws_iam_role_policy.deploy_worker.policy).Statement) == jsonencode([{
      Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:PutImage"], Resource = "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.project}/services/*"
    }]) && output.deploy_worker_role_arn == aws_iam_role.deploy_worker.arn
    error_message = "Deploy Worker may only read manifests and tag images in user service repositories, without Kubernetes/CodeBuild/layer push access."
  }
}
