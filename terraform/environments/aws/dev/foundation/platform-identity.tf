# Deploy Worker never receives direct Kubernetes, CodeBuild or ECR layer push access.
resource "aws_iam_role" "deploy_worker" {
  name        = "${var.project}-${var.environment}-deploy-worker"
  description = "Iris Deploy Worker: tag successful user service images"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
        "aws:RequestTag/kubernetes-namespace"       = "iris-platform"
        "aws:RequestTag/kubernetes-service-account" = "deploy-worker"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "deploy_worker" {
  name = "tag-successful-release"
  role = aws_iam_role.deploy_worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ecr:BatchGetImage", "ecr:PutImage"]
      Resource = local.service_repository_arns
    }]
  })
}
