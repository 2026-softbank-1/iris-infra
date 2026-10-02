locals {
  eks_cluster_names = [var.management_cluster_name, var.workload_cluster_name]
  eks_cluster_arns  = [for name in local.eks_cluster_names : "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/${name}"]
  eks_child_arns = flatten([for name in local.eks_cluster_names : [
    "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:nodegroup/${name}/*/*",
    "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:addon/${name}/*/*",
    "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:access-entry/${name}/*",
    "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:podidentityassociation/${name}/*"
  ]])
  eks_region = { "aws:RequestedRegion" = var.aws_region }
  runtime_names = concat(flatten([for name in local.eks_cluster_names : [for suffix in ["cluster", "node", "cni", "ebs", "lbc"] : "${name}-${suffix}"]]), [
    "${var.project}-${var.environment}-ssm-bridge", "${var.project}-${var.environment}-argocd-management",
    "${var.project}-${var.environment}-argocd-management-deploy", "${var.project}-${var.environment}-argocd-workload-deploy",
    "${var.project}-${var.environment}-deploy-worker", "${var.project}-${var.environment}-loki"
  ])
  runtime_role_arns   = [for name in local.runtime_names : "arn:aws:iam::${var.aws_account_id}:role/${name}"]
  runtime_policy_arns = [for name in local.eks_cluster_names : "arn:aws:iam::${var.aws_account_id}:policy/${name}-lbc"]
  attach_policy_arns = concat(local.runtime_policy_arns, [
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy", "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly", "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  ])
}
resource "aws_iam_policy" "eks_deployment" {
  name = "${var.project}-${var.environment}-eks-deployment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "DiscoverEks", Effect = "Allow", Action = ["eks:ListClusters", "eks:DescribeAddonVersions", "eks:DescribeAddonConfiguration", "eks:DescribeClusterVersions"], Resource = "*", Condition = { StringEquals = local.eks_region } },
    # CreateCluster does not support resource ARNs or a cluster-name condition.
    # Creation tags require TagResource below; subsequent operations keep named ARNs.
    { Sid = "CreateOwnedPrivateClusters", Effect = "Allow", Action = ["eks:CreateCluster"], Resource = "*", Condition = {
      StringEquals = merge(local.eks_region, {
        "aws:RequestTag/Project"   = var.project, "aws:RequestTag/Environment" = var.environment,
        "aws:RequestTag/ManagedBy" = "Terraform", "aws:RequestTag/Component" = "eks",
        "eks:authenticationMode"   = "API", "eks:supportType" = "STANDARD"
      }),
      Bool = {
        "eks:endpointPrivateAccess"                   = "true", "eks:endpointPublicAccess" = "false",
        "eks:bootstrapClusterCreatorAdminPermissions" = "false", "eks:bootstrapSelfManagedAddons" = "false"
      }
    } },
    { Sid = "ManageNamedClusters", Effect = "Allow", Action = ["eks:DescribeCluster", "eks:UpdateClusterConfig", "eks:UpdateClusterVersion", "eks:DeleteCluster", "eks:ListUpdates", "eks:DescribeUpdate", "eks:ListNodegroups", "eks:ListAddons", "eks:ListAccessEntries", "eks:ListPodIdentityAssociations", "eks:CreateNodegroup", "eks:CreateAddon", "eks:CreateAccessEntry", "eks:CreatePodIdentityAssociation"], Resource = local.eks_cluster_arns, Condition = { StringEquals = local.eks_region } },
    { Sid = "AddonPodIdentityCreation", Effect = "Allow", Action = ["eks:CreateAddon"], Resource = [for name in local.eks_cluster_names : "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:podidentityassociation/${name}/*"], Condition = { StringEquals = local.eks_region } },
    # Provider update waiters authorize both the cluster and the updated child.
    { Sid = "ReadChildUpdates", Effect = "Allow", Action = ["eks:DescribeUpdate", "eks:ListUpdates"], Resource = flatten([for name in local.eks_cluster_names : ["arn:aws:eks:${var.aws_region}:${var.aws_account_id}:nodegroup/${name}/*/*", "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:addon/${name}/*/*"]]), Condition = { StringEquals = local.eks_region } },
    { Sid = "ManageClusterChildren", Effect = "Allow", Action = ["eks:DescribeNodegroup", "eks:UpdateNodegroupConfig", "eks:UpdateNodegroupVersion", "eks:DeleteNodegroup", "eks:DescribeAddon", "eks:UpdateAddon", "eks:DeleteAddon", "eks:DescribeAccessEntry", "eks:UpdateAccessEntry", "eks:DeleteAccessEntry", "eks:AssociateAccessPolicy", "eks:DisassociateAccessPolicy", "eks:ListAssociatedAccessPolicies", "eks:DescribePodIdentityAssociation", "eks:UpdatePodIdentityAssociation", "eks:DeletePodIdentityAssociation"], Resource = local.eks_child_arns, Condition = { StringEquals = local.eks_region } },
    { Sid = "TagEks", Effect = "Allow", Action = ["eks:TagResource", "eks:UntagResource", "eks:ListTagsForResource"], Resource = concat(local.eks_cluster_arns, local.eks_child_arns), Condition = { StringEquals = local.eks_region } },
    { Sid = "ClusterLogs", Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:ListTagsForResource", "logs:TagResource", "logs:UntagResource", "logs:TagLogGroup", "logs:UntagLogGroup"], Resource = flatten([for name in local.eks_cluster_names : ["arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/eks/${name}/cluster", "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/eks/${name}/cluster:*"]]), Condition = { StringEquals = local.eks_region } },
    { Sid = "FindLogs", Effect = "Allow", Action = ["logs:DescribeLogGroups"], Resource = "*", Condition = { StringEquals = local.eks_region } }
  ] })
}
resource "aws_iam_policy" "runtime_iam" {
  name = "${var.project}-${var.environment}-runtime-iam"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "RuntimeRolesOnly", Effect = "Allow", Action = ["iam:CreateRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:DeleteRole", "iam:ListRoleTags", "iam:TagRole", "iam:UntagRole", "iam:ListRolePolicies", "iam:GetRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole"], Resource = local.runtime_role_arns },
    { Sid = "AttachReviewedPolicies", Effect = "Allow", Action = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"], Resource = local.runtime_role_arns, Condition = { ArnEquals = { "iam:PolicyARN" = local.attach_policy_arns } } },
    { Sid = "LbcPoliciesOnly", Effect = "Allow", Action = ["iam:CreatePolicy", "iam:GetPolicy", "iam:GetPolicyVersion", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:DeletePolicy", "iam:ListPolicyVersions", "iam:ListPolicyTags", "iam:TagPolicy", "iam:UntagPolicy"], Resource = local.runtime_policy_arns },
    { Sid = "PassEksRoles", Effect = "Allow", Action = ["iam:PassRole"], Resource = [for arn in local.runtime_role_arns : arn if !endswith(arn, "ssm-bridge") && !endswith(arn, "deploy-worker") && !endswith(arn, "loki")], Condition = { StringEquals = { "iam:PassedToService" = ["eks.amazonaws.com", "pods.eks.amazonaws.com", "ec2.amazonaws.com"] } } },
    { Sid = "PassBuildWorkerRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-build-worker", Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } },
    { Sid = "PassPodIdentityRoles", Effect = "Allow", Action = ["iam:PassRole"], Resource = [for name in ["deploy-worker", "loki"] : "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-${name}"], Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } },
    { Sid = "PassBridgeRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-ssm-bridge", Condition = { StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" } } },
    { Sid = "BridgeInstanceProfileOnly", Effect = "Allow", Action = ["iam:CreateInstanceProfile", "iam:GetInstanceProfile", "iam:DeleteInstanceProfile", "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile", "iam:ListInstanceProfileTags", "iam:TagInstanceProfile", "iam:UntagInstanceProfile"], Resource = "arn:aws:iam::${var.aws_account_id}:instance-profile/${var.project}-${var.environment}-ssm-bridge" },
    # Cluster OIDC providers have generated IDs; issuer prefix limits their scope.
    { Sid = "ClusterOidcOnly", Effect = "Allow", Action = ["iam:CreateOpenIDConnectProvider", "iam:GetOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider", "iam:UpdateOpenIDConnectProviderThumbprint", "iam:AddClientIDToOpenIDConnectProvider", "iam:RemoveClientIDFromOpenIDConnectProvider", "iam:ListOpenIDConnectProviderTags", "iam:TagOpenIDConnectProvider", "iam:UntagOpenIDConnectProvider"], Resource = "arn:aws:iam::${var.aws_account_id}:oidc-provider/oidc.eks.${var.aws_region}.amazonaws.com/id/*" },
    { Sid = "EksServiceLinkedRoles", Effect = "Allow", Action = ["iam:CreateServiceLinkedRole"], Resource = "arn:aws:iam::${var.aws_account_id}:role/aws-service-role/*", Condition = { StringEquals = { "iam:AWSServiceName" = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "autoscaling.amazonaws.com"] } } }
  ] })
}
resource "aws_iam_role_policy_attachment" "eks_deployment" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.eks_deployment.arn
}
resource "aws_iam_role_policy_attachment" "runtime_iam" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.runtime_iam.arn
}
