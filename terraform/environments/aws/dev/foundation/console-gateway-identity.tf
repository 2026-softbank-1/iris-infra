# Console Gateway opens a shell in user service Pods on the workload cluster (iris-was ADR 0033).
# It calls no AWS API: the identity only signs a `sts:GetCallerIdentity` URL locally so the workload EKS
# API accepts it as a bearer token. What the caller may do inside the cluster is decided there, by the
# Access Entry in the EKS module (Kubernetes group `iris-console`), not by IAM. So the role carries
# no permission policy and must stay that way. It is never shared with the API or the Workers.
resource "aws_iam_role" "console_gateway" {
  name        = "${var.project}-${var.environment}-console-gateway"
  description = "Iris Console Gateway: identity for the workload EKS Access Entry only; no AWS permissions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
        "aws:RequestTag/kubernetes-namespace"       = "iris-platform"
        "aws:RequestTag/kubernetes-service-account" = "console-gateway"
      } }
    }]
  })
}

output "console_gateway_role_arn" {
  description = "Console Gateway management Pod Identity role (iris-platform/console-gateway) and workload Access Entry principal"
  value       = aws_iam_role.console_gateway.arn
}
