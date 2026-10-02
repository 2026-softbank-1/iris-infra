locals {
  argocd_name           = "${var.project}-${var.environment}-argocd"
  argocd_management_arn = "arn:aws:iam::${var.aws_account_id}:role/${local.argocd_name}-management"
  argocd_deploy_arns    = { for purpose in ["management", "workload"] : purpose => "arn:aws:iam::${var.aws_account_id}:role/${local.argocd_name}-${purpose}-deploy" }
  argocd_tags           = { Project = var.project, Environment = var.environment, ManagedBy = "Terraform", Component = "gitops" }
}
resource "aws_iam_role" "argocd_management" {
  name = "${local.argocd_name}-management"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" }, Condition = { StringEquals = {
      "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name,
      "aws:RequestTag/kubernetes-namespace"       = "argocd",
      "aws:RequestTag/kubernetes-service-account" = ["argocd-server", "argocd-application-controller", "argocd-applicationset-controller"]
    } } },
    { Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { AWS = "*" }, Condition = { ArnEquals = { "aws:PrincipalArn" = local.argocd_management_arn } } }
  ] })
  tags = local.argocd_tags
}
resource "aws_iam_role_policy" "argocd_management" {
  name = "assume-cluster-identities"
  role = aws_iam_role.argocd_management.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Resource = concat(values(local.argocd_deploy_arns), [local.argocd_management_arn])
  }] })
}
resource "aws_iam_role" "argocd_deploy" {
  for_each = local.argocd_deploy_arns
  name     = "${local.argocd_name}-${each.key}-deploy"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { AWS = aws_iam_role.argocd_management.arn }
  }] })
  tags = local.argocd_tags
}
output "argocd_management_role_arn" { value = aws_iam_role.argocd_management.arn }
output "argocd_deploy_role_arns" { value = { for purpose, role in aws_iam_role.argocd_deploy : purpose => role.arn } }
