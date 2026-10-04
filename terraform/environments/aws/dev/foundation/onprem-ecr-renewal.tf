# Legacy on-prem VM namespace reconciler. Only the issuer runs in EKS; the
# workload receives tokens from repository-scoped STS sessions of the pull role.
resource "aws_iam_role" "onprem_ecr_renewer" {
  name = "${var.project}-${var.environment}-onprem-ecr-renewer"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow"
    Principal = { Service = "pods.eks.amazonaws.com" }
    Action    = ["sts:AssumeRole", "sts:TagSession"]
    Condition = { StringEquals = {
      "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
      "aws:RequestTag/kubernetes-namespace"       = "iris-platform"
      "aws:RequestTag/kubernetes-service-account" = "iris-onprem-ecr-renewer"
    } }
  }] })
}

resource "aws_iam_role" "onprem_ecr_renewal_pull" {
  name = "${var.project}-${var.environment}-onprem-ecr-renewal-pull"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow"
    Principal = { AWS = aws_iam_role.onprem_ecr_renewer.arn }
    Action    = ["sts:AssumeRole", "sts:TagSession"]
  }] })
}

resource "aws_iam_role_policy" "onprem_ecr_renewer" {
  name = "assume-scoped-image-pull"
  role = aws_iam_role.onprem_ecr_renewer.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.onprem_ecr_renewal_pull.arn
  }] })
}

resource "aws_iam_role_policy" "onprem_ecr_renewal_pull" {
  name = "pull-service-images"
  role = aws_iam_role.onprem_ecr_renewal_pull.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = local.service_repository_arns }
  ] })
}

output "onprem_ecr_renewer_role_arn" {
  description = "Legacy on-prem generic ECR reconciler management Pod Identity role"
  value       = aws_iam_role.onprem_ecr_renewer.arn
}
