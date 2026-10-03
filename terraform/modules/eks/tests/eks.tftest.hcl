mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/mock-policy" } }
  mock_resource "aws_iam_openid_connect_provider" { defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/MOCK" } }
  mock_resource "aws_launch_template" { defaults = { latest_version = 1, id = "lt-0123456789abcdef0" } }
  mock_resource "aws_eks_cluster" { defaults = {
    arn                   = "arn:aws:eks:ap-northeast-2:123456789012:cluster/mock"
    endpoint              = "https://example.eks.amazonaws.com"
    certificate_authority = [{ data = "Y2VydA==" }]
    identity              = [{ oidc = [{ issuer = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/MOCK" }] }]
  } }
}

variables {
  aws_account_id                     = "123456789012"
  aws_region                         = "ap-northeast-2"
  cluster_name                       = "iris-dev-management"
  vpc_id                             = "vpc-0123456789abcdef0"
  subnet_ids_by_az                   = { ap-northeast-2a = "subnet-0123456789abcdef0", ap-northeast-2c = "subnet-0123456789abcdef1" }
  service_cidr                       = "172.20.0.0/16"
  operator_principal_arn             = "arn:aws:iam::123456789012:role/operator"
  argocd_deploy_role_arn             = "arn:aws:iam::123456789012:role/argocd-deploy"
  bridge_security_group_id           = "sg-0123456789abcdef0"
  additional_node_security_group_ids = ["sg-0123456789abcdef1"]
}
run "private_multi_az" {
  command = apply
  assert {
    condition     = !aws_eks_cluster.this.vpc_config[0].endpoint_public_access && aws_eks_cluster.this.vpc_config[0].endpoint_private_access && !aws_eks_cluster.this.access_config[0].bootstrap_cluster_creator_admin_permissions && aws_eks_cluster.this.access_config[0].authentication_mode == "API"
    error_message = "API must be private and operator access explicit."
  }
  assert {
    condition     = length(aws_eks_node_group.az) == 2 && alltrue([for az, group in aws_eks_node_group.az : toset(group.subnet_ids) == toset([var.subnet_ids_by_az[az]]) && group.scaling_config[0].desired_size == 1 && group.scaling_config[0].max_size == 1 && toset(group.instance_types) == toset(["m7i-flex.large"])])
    error_message = "Each AZ must independently own exactly one Free-plan node."
  }
  assert {
    condition     = contains(aws_launch_template.node.vpc_security_group_ids, aws_eks_cluster.this.vpc_config[0].cluster_security_group_id) && contains(aws_launch_template.node.vpc_security_group_ids, var.additional_node_security_group_ids[0]) && aws_launch_template.node.metadata_options[0].http_tokens == "required"
    error_message = "Custom LT must retain cluster SG and source SG without exposing IMDS."
  }
  assert {
    condition     = strcontains(base64decode(aws_launch_template.node.user_data), "/etc/eks/nodeadm.d/99-iris-max-pods.yaml") && strcontains(base64decode(aws_launch_template.node.user_data), "maxPods: 35") && jsondecode(aws_eks_addon.cni.configuration_values).env.ENABLE_PREFIX_DELEGATION == "true" && jsondecode(aws_eks_addon.cni.configuration_values).enableNetworkPolicy == "true"
    error_message = "NodeConfig and CNI prefix/network policy configuration must agree."
  }
  assert {
    condition     = aws_eks_addon.cni.service_account_role_arn == aws_iam_role.cni.arn && !contains(keys(aws_iam_role_policy_attachment.node), "AmazonEKS_CNI_Policy") && aws_eks_addon.ebs.pod_identity_association == toset([{ role_arn = aws_iam_role.ebs.arn, service_account = "ebs-csi-controller-sa" }])
    error_message = "CNI bootstrap must use IRSA; EBS must use Pod Identity, not node credentials."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.bridge_api.from_port == 443 && aws_vpc_security_group_ingress_rule.bridge_api.referenced_security_group_id == var.bridge_security_group_id && aws_vpc_security_group_ingress_rule.bridge_api.cidr_ipv4 == null
    error_message = "Bridge access must be SG-referenced HTTPS only."
  }
  assert {
    condition     = aws_eks_access_entry.operator.principal_arn == var.operator_principal_arn && aws_eks_access_entry.argocd.principal_arn == var.argocd_deploy_role_arn && aws_eks_access_policy_association.argocd.access_scope[0].type == "cluster"
    error_message = "Explicit, separate operator and GitOps identities are required."
  }
}
run "extra_node_in_one_az" {
  command = apply
  variables {
    node_count_by_az = { "ap-northeast-2a" = 2 }
  }
  assert {
    condition     = aws_eks_node_group.az["ap-northeast-2a"].scaling_config[0].desired_size == 2 && aws_eks_node_group.az["ap-northeast-2a"].scaling_config[0].max_size == 2 && aws_eks_node_group.az["ap-northeast-2c"].scaling_config[0].desired_size == 1
    error_message = "Only the listed AZ gets the extra fixed node."
  }
}
run "reject_unknown_az_node_count" {
  command = plan
  variables {
    node_count_by_az = { "ap-northeast-2b" = 2 }
  }
  expect_failures = [var.node_count_by_az]
}
run "additional_operators" {
  command = plan
  variables { additional_operator_principal_arns = ["arn:aws:iam::123456789012:user/second-operator"] }
  assert {
    condition     = aws_eks_access_entry.additional_operator["arn:aws:iam::123456789012:user/second-operator"].type == "STANDARD" && aws_eks_access_policy_association.additional_operator["arn:aws:iam::123456789012:user/second-operator"].policy_arn == aws_eks_access_policy_association.operator.policy_arn && aws_eks_access_entry.operator.principal_arn == var.operator_principal_arn
    error_message = "Additional operators get the same cluster-admin entry without replacing the primary operator."
  }
}
run "reject_additional_session_or_duplicate" {
  command = plan
  variables { additional_operator_principal_arns = ["arn:aws:sts::123456789012:assumed-role/operator/session", "arn:aws:iam::123456789012:role/operator"] }
  expect_failures = [var.additional_operator_principal_arns]
}
run "reject_session_arn" {
  command = plan
  variables { operator_principal_arn = "arn:aws:sts::123456789012:assumed-role/operator/session" }
  expect_failures = [var.operator_principal_arn]
}
run "reject_ineligible_instance" {
  command = plan
  variables { node_instance_type = "t3.medium" }
  expect_failures = [var.node_instance_type]
}
run "reject_single_az" {
  command = plan
  variables { subnet_ids_by_az = { ap-northeast-2a = "subnet-0123456789abcdef0" } }
  expect_failures = [var.subnet_ids_by_az]
}
