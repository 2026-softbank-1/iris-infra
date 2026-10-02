mock_provider "aws" {
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/mock" } }
}

run "bridge_launch_authorization" {
  command = plan
  assert {
    condition = sort([for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement : statement.Sid]) == sort(["RunBridgeImage", "RunBridgeNetwork", "RunBridgeEni", "RunTaggedBridge", "RunTaggedBridgeVolume"]) && alltrue([
      for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement : statement.Effect == "Allow" && statement.Action == ["ec2:RunInstances"] && statement.Condition.StringEquals["aws:RequestedRegion"] == var.aws_region
    ])
    error_message = "Bridge launch must authorize all five resource statements individually and retain explicit regional RunInstances actions."
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement :
      statement.Resource == "arn:aws:ec2:${var.aws_region}::image/*" && jsonencode(statement.Condition) == jsonencode({ StringEquals = { "aws:RequestedRegion" = var.aws_region, "ec2:Owner" = "amazon" } }) if statement.Sid == "RunBridgeImage"
    ])
    error_message = "Amazon-owned AMI launch authorization requires the amazon owner alias, not the DescribeImages numeric OwnerId."
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement :
      statement.Resource == ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:subnet/*", "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:security-group/*"] && jsonencode(statement.Condition) == jsonencode({
        StringEquals = {
          "aws:RequestedRegion"         = var.aws_region
          "aws:ResourceTag/Project"     = var.project
          "aws:ResourceTag/Environment" = var.environment
          "aws:ResourceTag/ManagedBy"   = "Terraform"
        }
      }) if statement.Sid == "RunBridgeNetwork"
      ]) && alltrue([
      for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement :
      statement.Resource == "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:network-interface/*" && jsonencode(statement.Condition) == jsonencode({ StringEquals = { "aws:RequestedRegion" = var.aws_region } }) if statement.Sid == "RunBridgeEni"
    ])
    error_message = "Existing subnet/SG resources need owner resource tags; the transient ENI must have its own regional authorization without nonexistent owner tags."
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement :
      statement.Resource == ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/*"] && jsonencode(statement.Condition) == jsonencode({
        StringEquals = {
          "aws:RequestedRegion"        = var.aws_region
          "ec2:InstanceType"           = "t3.micro"
          "aws:RequestTag/Project"     = var.project
          "aws:RequestTag/Environment" = var.environment
          "aws:RequestTag/ManagedBy"   = "Terraform"
          "aws:RequestTag/Component"   = ["eks", "access"]
        }
      }) if statement.Sid == "RunTaggedBridge"
    ])
    error_message = "Bridge instance launches must remain t3.micro and require all four owner request tags."
  }
  assert {
    condition = length([for statement in jsondecode(aws_iam_policy.runtime_compute.policy).Statement : statement if statement.Sid == "TagsOnCreate"]) == 1 && alltrue([
      for statement in jsondecode(aws_iam_policy.runtime_compute.policy).Statement :
      statement.Effect == "Allow" && statement.Action == ["ec2:CreateTags"] && contains(statement.Resource, "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/*") && contains(statement.Resource, "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:volume/*") && statement.Condition.StringEquals["aws:RequestedRegion"] == var.aws_region && contains(statement.Condition.StringEquals["ec2:CreateAction"], "RunInstances") && statement.Condition.StringEquals["aws:RequestTag/Project"] == var.project && statement.Condition.StringEquals["aws:RequestTag/Environment"] == var.environment && statement.Condition.StringEquals["aws:RequestTag/ManagedBy"] == "Terraform" && statement.Condition.StringEquals["aws:RequestTag/Component"] == ["eks", "access"] if statement.Sid == "TagsOnCreate"
    ])
    error_message = "Launch-time CreateTags must authorize both instance and volume with the same owner request tags and RunInstances creation context."
  }
  assert {
    condition = length([for statement in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : statement if statement.Sid == "PassBridgeRole"]) == 1 && alltrue([
      for statement in jsondecode(aws_iam_policy.runtime_iam.policy).Statement :
      statement.Effect == "Allow" && statement.Action == ["iam:PassRole"] && statement.Resource == "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-ssm-bridge" && jsonencode(statement.Condition) == jsonencode({ StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" } }) if statement.Sid == "PassBridgeRole"
    ])
    error_message = "CI may pass only the named bridge role to EC2 through the bridge authorization."
  }
  assert {
    condition = length([for statement in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : statement if statement.Sid == "PassBuildWorkerRole"]) == 1 && alltrue([
      for statement in jsondecode(aws_iam_policy.runtime_iam.policy).Statement :
      statement.Effect == "Allow" && statement.Action == ["iam:PassRole"] && statement.Resource == "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-build-worker" && jsonencode(statement.Condition) == jsonencode({ StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } }) if statement.Sid == "PassBuildWorkerRole"
    ])
    error_message = "CI may pass the build worker role only to EKS Pod Identity."
  }
  assert {
    condition     = anytrue([for statement in jsondecode(aws_iam_policy.runtime_compute.policy).Statement : contains(statement.Action, "ec2:DescribeInstanceCreditSpecifications") if statement.Sid == "DiscoverCompute"])
    error_message = "The AWS provider reads T-family credit specifications after launching the bridge."
  }
}
variables {
  aws_account_id             = "123456789012"
  github_oidc_subject_prefix = "repo:example/infra"
}
run "runtime_boundaries_and_session" {
  command = apply
  assert {
    condition     = aws_iam_role.terraform_apply.max_session_duration == 7200 && alltrue([for policy in [aws_iam_policy.eks_deployment, aws_iam_policy.runtime_iam, aws_iam_policy.runtime_compute, aws_iam_policy.bridge_launch] : length(policy.policy) <= 6144])
    error_message = "Runtime policies must fit AWS size limits: eks=${length(aws_iam_policy.eks_deployment.policy)}, iam=${length(aws_iam_policy.runtime_iam.policy)}, compute=${length(aws_iam_policy.runtime_compute.policy)}, bridge=${length(aws_iam_policy.bridge_launch.policy)}; session must be 7200 seconds."
  }
  assert {
    condition     = !contains(local.runtime_role_arns, aws_iam_role.terraform_apply.arn) && alltrue([for statement in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : !contains(statement.Action, "iam:*")])
    error_message = "CI cannot administer its own role and runtime IAM actions must be explicit."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.runtime_iam.policy).Statement[1].Condition.ArnEquals["iam:PolicyARN"] == local.attach_policy_arns && !strcontains(aws_iam_policy.runtime_iam.policy, "token.actions.githubusercontent.com")
    error_message = "Only reviewed policies may attach; GitHub OIDC trust remains out of scope."
  }
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_policy.runtime_compute.policy).Statement : statement.Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2"])
    error_message = "Compute authorization must remain regional."
  }
  assert {
    condition = length([for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement : statement if statement.Sid == "RunTaggedBridgeVolume"]) == 1 && alltrue([
      for statement in jsondecode(aws_iam_policy.bridge_launch.policy).Statement :
      statement.Effect == "Allow" && statement.Action == ["ec2:RunInstances"] && statement.Resource == "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:volume/*" && jsonencode(statement.Condition) == jsonencode({
        StringEquals = {
          "aws:RequestedRegion"        = var.aws_region
          "aws:RequestTag/Project"     = var.project
          "aws:RequestTag/Environment" = var.environment
          "aws:RequestTag/ManagedBy"   = "Terraform"
          "aws:RequestTag/Component"   = ["eks", "access"]
        }
      }) if statement.Sid == "RunTaggedBridgeVolume"
    ])
    error_message = "Bridge volume RunInstances authorization must retain the exact account/region ARN and all four owner request tags; do not relax IAM to allow untagged volumes."
  }
}

run "eks_provider_permission_paths" {
  command = plan
  assert {
    condition     = length([for s in jsondecode(aws_iam_policy.runtime_compute.policy).Statement : s if s.Sid == "DiscoverCompute" && s.Resource == "*" && contains(s.Action, "ec2:DescribeInstanceCreditSpecifications") && s.Condition.StringEquals["aws:RequestedRegion"] == var.aws_region]) == 1
    error_message = "Burstable bridge refresh requires regional DescribeInstanceCreditSpecifications."
  }
  assert {
    condition = length([for s in jsondecode(aws_iam_policy.eks_deployment.policy).Statement : s if contains(s.Action, "eks:CreateCluster")]) == 1 && length([
      for s in jsondecode(aws_iam_policy.eks_deployment.policy).Statement : s if s.Sid == "CreateOwnedPrivateClusters" && s.Action == ["eks:CreateCluster"] && s.Resource == "*" && jsonencode(s.Condition) == jsonencode({
        StringEquals = {
          "aws:RequestedRegion"        = var.aws_region, "aws:RequestTag/Project" = var.project,
          "aws:RequestTag/Environment" = var.environment, "aws:RequestTag/ManagedBy" = "Terraform",
          "aws:RequestTag/Component"   = "eks", "eks:authenticationMode" = "API", "eks:supportType" = "STANDARD"
        },
        Bool = {
          "eks:endpointPrivateAccess"                   = "true", "eks:endpointPublicAccess" = "false",
          "eks:bootstrapClusterCreatorAdminPermissions" = "false", "eks:bootstrapSelfManagedAddons" = "false"
        }
      })
    ]) == 1
    error_message = "CreateCluster has no resource-level ARN support; its wildcard exception must require owned private API clusters with the approved authentication/bootstrap/support settings."
  }
  assert {
    condition = length([for s in jsondecode(aws_iam_policy.eks_deployment.policy).Statement : s if s.Sid == "ManageNamedClusters" && toset(s.Resource) == toset([
      "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/${var.management_cluster_name}",
      "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:cluster/${var.workload_cluster_name}"
      ]) && !contains(s.Action, "eks:CreateCluster")]) == 1 && length([
      for s in jsondecode(aws_iam_policy.eks_deployment.policy).Statement : s if s.Sid == "AddonPodIdentityCreation" && s.Action == ["eks:CreateAddon"] && toset(s.Resource) == toset([
        "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:podidentityassociation/${var.management_cluster_name}/*",
        "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:podidentityassociation/${var.workload_cluster_name}/*"
      ]) && s.Condition.StringEquals["aws:RequestedRegion"] == var.aws_region
    ]) == 1
    error_message = "EBS addon creation must authorize its Pod Identity resources while retaining the two named cluster boundary."
  }
  assert {
    condition = length([for s in jsondecode(aws_iam_policy.eks_deployment.policy).Statement : s if s.Sid == "ReadChildUpdates" && toset(s.Action) == toset(["eks:DescribeUpdate", "eks:ListUpdates"]) && toset(s.Resource) == toset(flatten([
      for name in [var.management_cluster_name, var.workload_cluster_name] : [
        "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:addon/${name}/*/*",
        "arn:aws:eks:${var.aws_region}:${var.aws_account_id}:nodegroup/${name}/*/*"
      ]
    ])) && s.Condition.StringEquals["aws:RequestedRegion"] == var.aws_region]) == 1
    error_message = "Provider nodegroup/addon update waiters require child update reads scoped to these two clusters."
  }
  assert {
    condition = !contains(local.runtime_role_arns, "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-build-worker") && length([
      for s in jsondecode(aws_iam_policy.runtime_iam.policy).Statement : s if s.Sid == "PassBuildWorkerRole" && s.Action == ["iam:PassRole"] && s.Resource == "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-build-worker" && jsonencode(s.Condition) == jsonencode({ StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } })
    ]) == 1
    error_message = "Management may pass the existing Build Worker to EKS Pod Identity only; its runtime IAM administration inventory must not expand."
  }
}
