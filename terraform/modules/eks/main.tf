locals {
  tags      = merge(var.tags, { Component = "eks" })
  ec2_trust = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }] })
}
resource "aws_iam_role" "cluster" {
  name               = "${var.cluster_name}-cluster"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "eks.amazonaws.com" } }] })
  tags               = local.tags
}
resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}
resource "aws_cloudwatch_log_group" "cluster" {
  name              = "/aws/eks/${var.cluster_name}/cluster"
  retention_in_days = 3
  tags              = local.tags
}
resource "aws_security_group" "api" {
  name        = "${var.cluster_name}-api"
  description = "Additional private API access for the SSM bridge"
  vpc_id      = var.vpc_id
  tags        = local.tags
}
resource "aws_vpc_security_group_ingress_rule" "bridge_api" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = var.bridge_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  tags                         = local.tags
}
resource "aws_eks_cluster" "this" {
  name                          = var.cluster_name
  version                       = var.kubernetes_version
  role_arn                      = aws_iam_role.cluster.arn
  bootstrap_self_managed_addons = false
  enabled_cluster_log_types     = ["api", "audit", "authenticator"]
  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }
  upgrade_policy { support_type = "STANDARD" }
  kubernetes_network_config {
    ip_family         = "ipv4"
    service_ipv4_cidr = var.service_cidr
  }
  vpc_config {
    subnet_ids              = values(var.subnet_ids_by_az)
    endpoint_private_access = true
    endpoint_public_access  = false
    security_group_ids      = concat([aws_security_group.api.id], var.additional_api_security_group_ids)
  }
  tags       = local.tags
  depends_on = [aws_iam_role_policy_attachment.cluster, aws_cloudwatch_log_group.cluster]
  timeouts { create = "40m" }
}
resource "aws_iam_openid_connect_provider" "cluster" {
  url            = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list = ["sts.amazonaws.com"]
  tags           = local.tags
}
resource "aws_iam_role" "cni" {
  name = "${var.cluster_name}-cni"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
      Principal = { Federated = aws_iam_openid_connect_provider.cluster.arn },
      Condition = { StringEquals = {
        "${trimprefix(aws_iam_openid_connect_provider.cluster.url, "https://")}:sub" = "system:serviceaccount:kube-system:aws-node",
        "${trimprefix(aws_iam_openid_connect_provider.cluster.url, "https://")}:aud" = "sts.amazonaws.com"
      } }
    }]
  })
  tags = local.tags
}
resource "aws_iam_role_policy_attachment" "cni" {
  role       = aws_iam_role.cni.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}
# CNI credentials exist before any node boots; CoreDNS waits until compute exists.
resource "aws_eks_addon" "cni" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "vpc-cni"
  addon_version               = "v1.22.4-eksbuild.3"
  service_account_role_arn    = aws_iam_role.cni.arn
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
  configuration_values = jsonencode({
    enableNetworkPolicy = "true",
    env                 = { ENABLE_PREFIX_DELEGATION = "true", WARM_PREFIX_TARGET = "1", NETWORK_POLICY_ENFORCING_MODE = "standard" }
  })
  tags       = local.tags
  depends_on = [aws_iam_role_policy_attachment.cni]
}
resource "aws_iam_role" "node" {
  name               = "${var.cluster_name}-node"
  assume_role_policy = local.ec2_trust
  tags               = local.tags
}
resource "aws_iam_role_policy_attachment" "node" {
  for_each   = toset(["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly"])
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}
resource "aws_security_group" "node" {
  name        = "${var.cluster_name}-node"
  description = "Node egress; EKS cluster SG supplies control plane and node traffic"
  vpc_id      = var.vpc_id
  tags        = local.tags
}
resource "aws_vpc_security_group_egress_rule" "node" {
  security_group_id = aws_security_group.node.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  tags              = local.tags
}
resource "aws_launch_template" "node" {
  name                   = "${var.cluster_name}-node"
  update_default_version = true
  vpc_security_group_ids = concat([aws_eks_cluster.this.vpc_config[0].cluster_security_group_id, aws_security_group.node.id], var.additional_node_security_group_ids)
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  # AL2023 nodeadm-run executes after cloud-final and merges these drop-ins.
  # MNG supplies the cluster NodeConfig; this final drop-in limits pod density.
  user_data = base64encode(<<-MIME
    MIME-Version: 1.0
    Content-Type: multipart/mixed; boundary="IRIS"

    --IRIS
    Content-Type: text/x-shellscript; charset="us-ascii"

    #!/bin/bash
    set -euo pipefail
    install -d -m 0755 /etc/eks/nodeadm.d
    cat > /etc/eks/nodeadm.d/99-iris-max-pods.yaml <<'CONFIG'
    apiVersion: node.eks.aws/v1alpha1
    kind: NodeConfig
    spec:
      kubelet:
        config:
          maxPods: 35
    CONFIG
    --IRIS--
  MIME
  )
  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${var.cluster_name}-node" })
  }
  tag_specifications {
    resource_type = "volume"
    tags          = local.tags
  }
  tags = local.tags
}
resource "aws_eks_node_group" "az" {
  for_each        = var.subnet_ids_by_az
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.cluster_name}-${each.key}"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = [each.value]
  instance_types  = [var.node_instance_type]
  capacity_type   = "ON_DEMAND"
  ami_type        = "AL2023_x86_64_STANDARD"
  version         = var.kubernetes_version
  release_version = var.node_release_version
  launch_template {
    id      = aws_launch_template.node.id
    version = tostring(aws_launch_template.node.latest_version)
  }
  scaling_config {
    min_size     = lookup(var.node_count_by_az, each.key, 1)
    desired_size = lookup(var.node_count_by_az, each.key, 1)
    max_size     = lookup(var.node_count_by_az, each.key, 1)
  }
  update_config { max_unavailable = 1 }
  node_repair_config { enabled = true }
  labels     = { "iris.dev/cluster" = var.cluster_name }
  tags       = local.tags
  depends_on = [aws_iam_role_policy_attachment.node, aws_eks_addon.cni]
  timeouts { create = "35m" }
}
resource "aws_eks_addon" "base" {
  for_each = {
    coredns                = "v1.13.2-eksbuild.31"
    kube-proxy             = "v1.35.3-eksbuild.29"
    eks-pod-identity-agent = "v1.3.10-eksbuild.3"
  }
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.key
  addon_version               = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
  tags                        = local.tags
  depends_on                  = [aws_eks_node_group.az]
}
locals {
  pod_trust = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" },
    Condition = { StringEquals = { "aws:RequestTag/eks-cluster-name" = var.cluster_name, "aws:RequestTag/kubernetes-namespace" = "kube-system" } }
  }] })
}
resource "aws_iam_role" "ebs" {
  name = "${var.cluster_name}-ebs"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" },
    Condition = { StringEquals = { "aws:RequestTag/eks-cluster-name" = var.cluster_name, "aws:RequestTag/kubernetes-namespace" = "kube-system", "aws:RequestTag/kubernetes-service-account" = "ebs-csi-controller-sa" } }
  }] })
  tags = local.tags
}
resource "aws_iam_role_policy_attachment" "ebs" {
  role       = aws_iam_role.ebs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}
resource "aws_eks_addon" "ebs" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "aws-ebs-csi-driver"
  addon_version               = "v1.66.0-eksbuild.1"
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
  pod_identity_association {
    role_arn        = aws_iam_role.ebs.arn
    service_account = "ebs-csi-controller-sa"
  }
  tags       = local.tags
  depends_on = [aws_eks_addon.base, aws_iam_role_policy_attachment.ebs]
}
resource "aws_iam_role" "lbc" {
  name = "${var.cluster_name}-lbc"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" },
    Condition = { StringEquals = { "aws:RequestTag/eks-cluster-name" = var.cluster_name, "aws:RequestTag/kubernetes-namespace" = "kube-system", "aws:RequestTag/kubernetes-service-account" = "aws-load-balancer-controller" } }
  }] })
  tags = local.tags
}
resource "aws_iam_policy" "lbc" {
  name   = "${var.cluster_name}-lbc"
  policy = jsonencode(jsondecode(file("${path.module}/../../config/aws-load-balancer-controller-policy.json")))
  tags   = local.tags
}
resource "aws_iam_role_policy_attachment" "lbc" {
  role       = aws_iam_role.lbc.name
  policy_arn = aws_iam_policy.lbc.arn
}
resource "aws_eks_pod_identity_association" "lbc" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.lbc.arn
  depends_on      = [aws_eks_addon.base, aws_iam_role_policy_attachment.lbc]
}
resource "aws_eks_access_entry" "operator" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.operator_principal_arn
  type          = "STANDARD"
  tags          = local.tags
}
resource "aws_eks_access_policy_association" "operator" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_eks_access_entry.operator.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
}
resource "aws_eks_access_entry" "additional_operator" {
  for_each      = toset(var.additional_operator_principal_arns)
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value
  type          = "STANDARD"
  tags          = local.tags
}
resource "aws_eks_access_policy_association" "additional_operator" {
  for_each      = aws_eks_access_entry.additional_operator
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = each.value.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
}
resource "aws_eks_access_entry" "argocd" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = var.argocd_deploy_role_arn
  type          = "STANDARD"
  tags          = local.tags
}
resource "aws_eks_access_policy_association" "argocd" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_eks_access_entry.argocd.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
}
