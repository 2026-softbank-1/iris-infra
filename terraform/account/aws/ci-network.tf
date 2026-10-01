# Network resources live in foundation; this policy must be applied by an
# administrator before merging network code into main. CI cannot edit itself.
locals {
  network_ec2_arn = "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}"
  network_region  = { "aws:RequestedRegion" = var.aws_region }
  network_owner = {
    "aws:ResourceTag/Project"     = var.project
    "aws:ResourceTag/Environment" = var.environment
    "aws:ResourceTag/ManagedBy"   = "Terraform"
    "aws:ResourceTag/Component"   = "network"
  }
  network_request_owner = {
    "aws:RequestTag/Project"     = var.project
    "aws:RequestTag/Environment" = var.environment
    "aws:RequestTag/ManagedBy"   = "Terraform"
    "aws:RequestTag/Component"   = "network"
  }
  network_create_actions = {
    vpc                 = ["ec2:CreateVpc"]
    subnet              = ["ec2:CreateSubnet"]
    route-table         = ["ec2:CreateRouteTable"]
    internet-gateway    = ["ec2:CreateInternetGateway"]
    elastic-ip          = ["ec2:AllocateAddress"]
    natgateway          = ["ec2:CreateNatGateway"]
    security-group      = ["ec2:CreateSecurityGroup"]
    security-group-rule = ["ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress"]
  }
  # Multi-resource APIs authorize existing parents separately from new objects.
  # RequestTag conditions belong on the new resource; ResourceTag on the parent.
  network_manage_actions = {
    vpc = [
      "ec2:CreateSubnet", "ec2:CreateRouteTable", "ec2:CreateSecurityGroup", "ec2:CreateNatGateway",
      "ec2:ModifyVpcAttribute", "ec2:DeleteVpc", "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
    ]
    subnet = [
      "ec2:ModifySubnetAttribute", "ec2:DeleteSubnet", "ec2:CreateNatGateway",
      "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation",
    ]
    route-table = [
      "ec2:DeleteRouteTable", "ec2:CreateRoute", "ec2:ReplaceRoute", "ec2:DeleteRoute",
      "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation",
    ]
    internet-gateway = ["ec2:AttachInternetGateway", "ec2:DetachInternetGateway", "ec2:DeleteInternetGateway"]
    elastic-ip       = ["ec2:ReleaseAddress", "ec2:CreateNatGateway"]
    natgateway       = ["ec2:DeleteNatGateway"]
    security-group = [
      "ec2:DeleteSecurityGroup", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:ModifySecurityGroupRules",
      "ec2:UpdateSecurityGroupRuleDescriptionsIngress", "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
    ]
    security-group-rule = ["ec2:ModifySecurityGroupRules"]
  }
}

# A managed policy avoids consuming the role's 10,240-character inline quota
# shared by the existing bootstrap/state and build policies.
resource "aws_iam_policy" "foundation_network" {
  name        = "${var.project}-${var.environment}-foundation-network"
  description = "Tagged foundation network resources in the deployment account and region"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid    = "ReadRegionalNetwork"
          Effect = "Allow"
          # These discovery APIs do not support resource-level authorization.
          Action = [
            "ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:DescribeRouteTables",
            "ec2:DescribeInternetGateways", "ec2:DescribeAddresses", "ec2:DescribeAddressesAttribute",
            "ec2:DescribeNatGateways", "ec2:DescribeSecurityGroups", "ec2:DescribeSecurityGroupRules",
            "ec2:DescribeNetworkInterfaces", "ec2:DescribeTags",
          ]
          Resource  = "*"
          Condition = { StringEquals = local.network_region }
        },
        {
          Sid       = "ReadVpcAttributes"
          Effect    = "Allow"
          Action    = ["ec2:DescribeVpcAttribute"]
          Resource  = "${local.network_ec2_arn}:vpc/*"
          Condition = { StringEquals = merge(local.network_region, local.network_owner) }
        },
        {
          Sid      = "TagNetworkOnCreation"
          Effect   = "Allow"
          Action   = ["ec2:CreateTags"]
          Resource = [for kind in keys(local.network_create_actions) : "${local.network_ec2_arn}:${kind}/*"]
          Condition = {
            StringEquals = merge(local.network_region, local.network_request_owner, {
              "ec2:CreateAction" = [for action in flatten(values(local.network_create_actions)) : trimprefix(action, "ec2:")]
            })
          }
        },
        {
          Sid      = "UpdateOwnedNetworkTags"
          Effect   = "Allow"
          Action   = ["ec2:CreateTags"]
          Resource = [for kind in keys(local.network_create_actions) : "${local.network_ec2_arn}:${kind}/*"]
          Condition = {
            StringEquals = merge(local.network_region, local.network_owner)
            # Ownership tags can be reasserted, but cannot be changed to escape scope.
            StringEqualsIfExists = local.network_request_owner
          }
        },
        {
          Sid      = "DeleteNonOwnershipNetworkTags"
          Effect   = "Allow"
          Action   = ["ec2:DeleteTags"]
          Resource = [for kind in keys(local.network_create_actions) : "${local.network_ec2_arn}:${kind}/*"]
          Condition = {
            StringEquals = merge(local.network_region, local.network_owner)
            "ForAllValues:StringNotEquals" = {
              "aws:TagKeys" = ["Project", "Environment", "ManagedBy", "Component"]
            }
          }
        },
      ],
      [{
        Sid       = "CreateNetworkResources"
        Effect    = "Allow"
        Action    = distinct(flatten(values(local.network_create_actions)))
        Resource  = [for kind in keys(local.network_create_actions) : "${local.network_ec2_arn}:${kind}/*"]
        Condition = { StringEquals = merge(local.network_region, local.network_request_owner) }
      }],
      [{
        Sid       = "ManageOwnedNetworkResources"
        Effect    = "Allow"
        Action    = distinct(flatten(values(local.network_manage_actions)))
        Resource  = [for kind in keys(local.network_manage_actions) : "${local.network_ec2_arn}:${kind}/*"]
        Condition = { StringEquals = merge(local.network_region, local.network_owner) }
      }]
    )
  })
}

resource "aws_iam_role_policy_attachment" "foundation_network" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.foundation_network.arn
}
