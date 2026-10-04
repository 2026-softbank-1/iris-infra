# Reuse the existing manually provisioned svc28 role and Pod Identity association.
# Terraform owns only this additional inline policy, never that role or its trust.
locals {
  onprem_ecr_legacy_role_name = "${var.project}-${var.environment}-onprem-ecr-svc-28"
  onprem_ecr_legacy_role_arn  = "arn:aws:iam::${var.aws_account_id}:role/${local.onprem_ecr_legacy_role_name}"
}

resource "aws_iam_role_policy" "onprem_ecr_renewer" {
  name = "assume-scoped-image-pull"
  role = local.onprem_ecr_legacy_role_name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.onprem_ecr_pull.arn
  }] })
}
