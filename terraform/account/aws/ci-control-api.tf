# Administrators apply account before foundation. The Control API role only reads build logs,
# so CI may administer that one role and pass it only to EKS Pod Identity.
locals {
  control_api_role_arn = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-${var.environment}-control-api"
}

resource "aws_iam_policy" "control_api" {
  name = "${var.project}-${var.environment}-control-api-deployment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "ControlApiRole", Effect = "Allow", Action = ["iam:CreateRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:DeleteRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags"], Resource = local.control_api_role_arn },
    { Sid = "PassControlApiRole", Effect = "Allow", Action = ["iam:PassRole"], Resource = local.control_api_role_arn, Condition = { StringEquals = { "iam:PassedToService" = "pods.eks.amazonaws.com" } } }
  ] })
}

resource "aws_iam_role_policy_attachment" "control_api" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.control_api.arn
}
