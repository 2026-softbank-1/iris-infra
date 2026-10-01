mock_provider "aws" {
  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::123456789012:policy/iris-dev-foundation-network" }
  }
}

variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example@123/infra@456"
}

run "scoped_network_deployment" {
  command = apply

  assert {
    condition = (
      length(aws_iam_policy.foundation_network.policy) <= 6144 &&
      aws_iam_role_policy_attachment.foundation_network.role == aws_iam_role.terraform_apply.name &&
      aws_iam_role_policy_attachment.foundation_network.policy_arn == aws_iam_policy.foundation_network.arn
    )
    error_message = "Network permissions must fit the managed-policy size quota and attach to the existing deployment role."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
      statement.Effect == "Allow" &&
      statement.Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2" &&
      alltrue([for action in statement.Action : startswith(action, "ec2:") && !strcontains(action, "*") &&
        !contains(["ec2:RunInstances", "ec2:CreateNetworkInterface", "ec2:DeleteNetworkInterface", "ec2:ModifyNetworkInterfaceAttribute"], action)
      ]) &&
      (statement.Resource != "*" || (
        statement.Sid == "ReadRegionalNetwork" && alltrue([for action in statement.Action : startswith(action, "ec2:Describe")])
      ))
    ])
    error_message = "Only explicit regional network EC2 actions are permitted; wildcard resources are for discovery only."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
      alltrue([
        for resource in try(tolist(statement.Resource), [tostring(statement.Resource)]) :
        contains([
          "arn:aws:ec2:ap-northeast-2:123456789012:vpc/*",
          "arn:aws:ec2:ap-northeast-2:123456789012:subnet/*",
          "arn:aws:ec2:ap-northeast-2:123456789012:route-table/*",
          "arn:aws:ec2:ap-northeast-2:123456789012:internet-gateway/*",
          "arn:aws:ec2:ap-northeast-2:123456789012:elastic-ip/*",
          "arn:aws:ec2:ap-northeast-2:123456789012:natgateway/*",
          "arn:aws:ec2:ap-northeast-2:123456789012:security-group/*",
          "arn:aws:ec2:ap-northeast-2:123456789012:security-group-rule/*",
        ], resource)
      ]) if statement.Resource != "*"
    ])
    error_message = "Resource scope must not include other accounts, regions, instance/ENI resources or non-EC2 services."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
      alltrue([for key, value in { Project = "iris", Environment = "dev", ManagedBy = "Terraform", Component = "network" } :
        try(statement.Condition.StringEquals["aws:RequestTag/${key}"], "") == value
      ]) if contains(["CreateNetworkResources", "TagNetworkOnCreation"], statement.Sid)
    ])
    error_message = "New resources and tags-on-create must carry all four ownership tags."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
      alltrue([for key, value in { Project = "iris", Environment = "dev", ManagedBy = "Terraform", Component = "network" } :
        try(statement.Condition.StringEquals["aws:ResourceTag/${key}"], "") == value
      ]) if contains(["ReadVpcAttributes", "ManageOwnedNetworkResources", "UpdateOwnedNetworkTags", "DeleteNonOwnershipNetworkTags"], statement.Sid)
    ])
    error_message = "Existing parents, updates and deletion must be restricted to owned network resources."
  }

  assert {
    condition = (
      alltrue([for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
        statement.Action == ["ec2:CreateTags"] &&
        toset(statement.Condition.StringEquals["ec2:CreateAction"]) == toset([
          "CreateVpc", "CreateSubnet", "CreateRouteTable", "CreateInternetGateway", "AllocateAddress",
          "CreateNatGateway", "CreateSecurityGroup", "AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress",
        ]) if statement.Sid == "TagNetworkOnCreation"
      ]) &&
      alltrue([for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
        statement.Condition.StringEqualsIfExists == {
          "aws:RequestTag/Project"   = "iris", "aws:RequestTag/Environment" = "dev",
          "aws:RequestTag/ManagedBy" = "Terraform", "aws:RequestTag/Component" = "network",
        } if statement.Sid == "UpdateOwnedNetworkTags"
      ]) &&
      alltrue([for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
        toset(statement.Condition["ForAllValues:StringNotEquals"]["aws:TagKeys"]) == toset(["Project", "Environment", "ManagedBy", "Component"])
        if statement.Sid == "DeleteNonOwnershipNetworkTags"
      ])
    )
    error_message = "Tags-on-create must only accompany allowed create APIs, and ownership tags must not be overwritten or deleted."
  }

  assert {
    condition = (
      length([for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement : statement if statement.Sid == "CreateNetworkResources"]) == 1 &&
      length([for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement : statement if statement.Sid == "ManageOwnedNetworkResources"]) == 1 &&
      alltrue([for statement in jsondecode(aws_iam_policy.foundation_network.policy).Statement :
        contains(statement.Action, "ec2:DeleteNatGateway") && contains(statement.Action, "ec2:ReleaseAddress") &&
        contains(statement.Action, "ec2:DeleteVpc") && contains(statement.Action, "ec2:ModifySecurityGroupRules") &&
        contains(statement.Action, "ec2:RevokeSecurityGroupEgress") && contains(statement.Action, "ec2:ReplaceRouteTableAssociation") &&
        contains(statement.Action, "ec2:CreateSubnet") && contains(statement.Action, "ec2:CreateNatGateway")
        if statement.Sid == "ManageOwnedNetworkResources"
      ])
    )
    error_message = "Scoped creation-parent, update and teardown permissions must exist, including default SG egress revocation."
  }
}
