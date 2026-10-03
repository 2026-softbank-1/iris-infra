# Temporary competition access. Persist false in account inputs and apply to revoke.
resource "aws_iam_role_policy_attachment" "temporary_admin" {
  count = var.enable_temporary_admin_access ? 1 : 0

  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
