# Control API shows build logs on the deployment details screen (iris-was ADR 0021).
# It reads CloudWatch log events only: no CodeBuild, ECR, S3 or Kubernetes access, and no log writes.
# The Build Worker role has its own log-read statement for AI diagnosis and is never shared.
resource "aws_iam_role" "control_api" {
  name        = "${var.project}-${var.environment}-control-api"
  description = "Iris Control API: read CodeBuild logs for the deployment details screen"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
        "aws:RequestTag/kubernetes-namespace"       = "iris-platform"
        "aws:RequestTag/kubernetes-service-account" = "iris-platform-api"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "control_api" {
  name = "read-build-logs"
  role = aws_iam_role.control_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      # The group ARN with `:*` covers its log streams. Reads only.
      Sid      = "ReadBuildLogs"
      Effect   = "Allow"
      Action   = ["logs:GetLogEvents"]
      Resource = "${aws_cloudwatch_log_group.build.arn}:*"
    }]
  })
}

output "control_api_role_arn" {
  description = "Control API management Pod Identity role (iris-platform/iris-platform-api)"
  value       = aws_iam_role.control_api.arn
}
