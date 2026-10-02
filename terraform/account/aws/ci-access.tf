locals {
  compute_prefix  = "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}"
  compute_owner   = { "aws:ResourceTag/Project" = var.project, "aws:ResourceTag/Environment" = var.environment, "aws:ResourceTag/ManagedBy" = "Terraform", "aws:ResourceTag/Component" = ["eks", "access"] }
  compute_request = { "aws:RequestTag/Project" = var.project, "aws:RequestTag/Environment" = var.environment, "aws:RequestTag/ManagedBy" = "Terraform", "aws:RequestTag/Component" = ["eks", "access"] }
  compute_kinds   = ["instance", "volume", "network-interface", "launch-template", "security-group", "security-group-rule"]
}
resource "aws_iam_policy" "runtime_compute" {
  name = "${var.project}-${var.environment}-runtime-compute"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "DiscoverCompute", Effect = "Allow", Action = ["ec2:DescribeInstances", "ec2:DescribeInstanceAttribute", "ec2:DescribeInstanceStatus", "ec2:DescribeInstanceTypes", "ec2:DescribeInstanceTypeOfferings", "ec2:DescribeImages", "ec2:DescribeVolumes", "ec2:DescribeLaunchTemplates", "ec2:DescribeLaunchTemplateVersions", "ec2:DescribeNetworkInterfaces", "ec2:DescribeSecurityGroups", "ec2:DescribeSecurityGroupRules", "ec2:DescribeTags", "ec2:DescribeSubnets", "ec2:DescribeVpcs"], Resource = "*", Condition = { StringEquals = local.eks_region } },
    { Sid = "CreateOwnedSecurity", Effect = "Allow", Action = ["ec2:CreateSecurityGroup", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress", "ec2:CreateLaunchTemplate"], Resource = [for kind in ["security-group", "security-group-rule", "launch-template"] : "${local.compute_prefix}:${kind}/*"], Condition = { StringEquals = merge(local.eks_region, local.compute_request) } },
    { Sid = "NetworkParents", Effect = "Allow", Action = ["ec2:CreateSecurityGroup", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress"], Resource = ["${local.compute_prefix}:vpc/*", "${local.compute_prefix}:security-group/*"], Condition = { StringEquals = merge(local.eks_region, { "aws:ResourceTag/Project" = var.project, "aws:ResourceTag/Environment" = var.environment, "aws:ResourceTag/ManagedBy" = "Terraform" }) } },
    { Sid = "MaintainOwnedCompute", Effect = "Allow", Action = ["ec2:DeleteSecurityGroup", "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:ModifySecurityGroupRules", "ec2:DeleteLaunchTemplate", "ec2:CreateLaunchTemplateVersion", "ec2:DeleteLaunchTemplateVersions", "ec2:ModifyLaunchTemplate", "ec2:TerminateInstances", "ec2:ModifyInstanceAttribute", "ec2:ModifyInstanceCreditSpecification", "ec2:StopInstances", "ec2:StartInstances"], Resource = [for kind in local.compute_kinds : "${local.compute_prefix}:${kind}/*"], Condition = { StringEquals = merge(local.eks_region, local.compute_owner) } },
    { Sid = "TagsOnCreate", Effect = "Allow", Action = ["ec2:CreateTags"], Resource = [for kind in local.compute_kinds : "${local.compute_prefix}:${kind}/*"], Condition = { StringEquals = merge(local.eks_region, local.compute_request, { "ec2:CreateAction" = ["RunInstances", "CreateLaunchTemplate", "CreateSecurityGroup", "AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress"] }) } },
    { Sid = "UpdateOwnedTags", Effect = "Allow", Action = ["ec2:CreateTags"], Resource = [for kind in local.compute_kinds : "${local.compute_prefix}:${kind}/*"], Condition = { StringEquals = merge(local.eks_region, local.compute_owner), StringEqualsIfExists = local.compute_request } },
    { Sid = "DeleteNonOwnerTags", Effect = "Allow", Action = ["ec2:DeleteTags"], Resource = [for kind in local.compute_kinds : "${local.compute_prefix}:${kind}/*"], Condition = { StringEquals = merge(local.eks_region, local.compute_owner), "ForAllValues:StringNotEquals" = { "aws:TagKeys" = ["Project", "Environment", "ManagedBy", "Component"] } } },
    { Sid = "ReadBridgeAmi", Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "arn:aws:ssm:${var.aws_region}::parameter/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64", Condition = { StringEquals = local.eks_region } }
  ] })
}
resource "aws_iam_role_policy_attachment" "runtime_compute" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.runtime_compute.arn
}

resource "aws_iam_policy" "bridge_launch" {
  name = "${var.project}-${var.environment}-bridge-launch"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    # EC2 uses the "amazon" owner alias when authorizing Amazon-owned AMIs.
    { Sid = "RunBridgeImage", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "arn:aws:ec2:${var.aws_region}::image/*", Condition = { StringEquals = merge(local.eks_region, { "ec2:Owner" = "amazon" }) } },
    { Sid = "RunBridgeNetwork", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = ["${local.compute_prefix}:subnet/*", "${local.compute_prefix}:security-group/*"], Condition = { StringEquals = merge(local.eks_region, { "aws:ResourceTag/Project" = var.project, "aws:ResourceTag/Environment" = var.environment, "aws:ResourceTag/ManagedBy" = "Terraform" }) } },
    # EC2 requires separate authorization for the untagged transient ENI.
    { Sid = "RunBridgeEni", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "${local.compute_prefix}:network-interface/*", Condition = { StringEquals = local.eks_region } },
    { Sid = "RunTaggedBridge", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = ["${local.compute_prefix}:instance/*"], Condition = { StringEquals = merge(local.eks_region, local.compute_request, { "ec2:InstanceType" = "t3.micro" }) } },
    { Sid = "RunTaggedBridgeVolume", Effect = "Allow", Action = ["ec2:RunInstances"], Resource = "${local.compute_prefix}:volume/*", Condition = { StringEquals = merge(local.eks_region, local.compute_request) } },
  ] })
}
resource "aws_iam_role_policy_attachment" "bridge_launch" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.bridge_launch.arn
}
