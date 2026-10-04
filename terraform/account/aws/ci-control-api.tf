# Administrators apply account before foundation. The Control API role only reads build logs,
# and CI also administers the two reviewed legacy on-prem renewal roles here.
# Reuse this document: the account-plan simulator accepts at most ten documents.
# The on-prem ECR pull role is assumed by the Control API (STS), never passed to a service.
# The Console Gateway role has no permission policy (it only signs a caller identity for the workload EKS
# Access Entry), so CI may create/maintain it and pass it only to EKS Pod Identity, but never put or attach
# a policy on it. EKS Access Entry and Pod Identity association ARNs are covered by ci-eks.tf.
locals {
  console_gateway_role_arn         = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-console-gateway"
  control_api_role_arn             = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-control-api"
  onprem_ecr_pull_role_arn         = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-onprem-ecr-pull"
  onprem_ecr_renewer_role_arn      = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-onprem-ecr-renewer"
  onprem_ecr_renewal_pull_role_arn = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-onprem-ecr-renewal-pull"
}

resource "aws_iam_policy" "control_api" {
  name = "${var.project}-${var.environment}-control-api-deployment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "ControlApiRole", Effect = "Allow", Action = ["iam:CreateRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:DeleteRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags"], Resource = [local.control_api_role_arn, local.onprem_ecr_pull_role_arn, local.onprem_ecr_renewer_role_arn, local.onprem_ecr_renewal_pull_role_arn] },
    { Sid = "PassControlApiRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.control_api_role_arn, Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } },
    { Sid = "PassOnpremEcrRenewerRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.onprem_ecr_renewer_role_arn, Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } },
    { Sid = "ConsoleGatewayRole", Effect = "Allow", Action = ["iam:CreateRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:DeleteRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags"], Resource = local.console_gateway_role_arn },
    { Sid = "PassConsoleGatewayRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.console_gateway_role_arn, Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } }
  ] })
}

resource "aws_iam_role_policy_attachment" "control_api" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.control_api.arn
}
