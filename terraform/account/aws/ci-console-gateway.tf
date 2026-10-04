# Administrators apply account before foundation. The Console Gateway role carries no permission policy
# (it only signs a caller identity for the workload EKS Access Entry), so CI may administer that one role
# and pass it only to EKS Pod Identity. EKS Access Entry and Pod Identity association ARNs are already
# covered by the EKS deployment policy.
locals {
  console_gateway_role_arn = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-console-gateway"
}

resource "aws_iam_policy" "console_gateway" {
  name = "${var.project}-${var.environment}-console-gateway-deployment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "ConsoleGatewayRole", Effect = "Allow", Action = ["iam:CreateRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:DeleteRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags"], Resource = local.console_gateway_role_arn },
    { Sid = "PassConsoleGatewayRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.console_gateway_role_arn, Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } }
  ] })
}

resource "aws_iam_role_policy_attachment" "console_gateway" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.console_gateway.arn
}
