# User-registered on-prem servers pull their services' images from ECR (iris-was onprem-server-registration
# contract §5). A server asks the Control API for registry credentials; the API assumes this role with a session
# policy that names only the repositories of the services on that server, then returns an ECR token.
# The existing legacy renewal job also assumes this role with a per-service session policy.
# The role itself allows pull on every user service repository and nothing else; the session policy narrows it.
# Control API credentials already come from a role session (Pod Identity), so this is role chaining and a
# session lasts at most one hour. Pod Identity session tags are transitive, so the trust policy must also
# allow sts:TagSession or AssumeRole is denied.
resource "aws_iam_role" "onprem_ecr_pull" {
  name        = "${var.project}-${var.environment}-onprem-ecr-pull"
  description = "Iris on-prem ECR pull tokens, narrowed by per-server or per-service session policy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { AWS = aws_iam_role.control_api.arn }
      }, {
      Sid       = "ExistingOnpremRenewal"
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { AWS = local.onprem_ecr_legacy_role_arn }
      Condition = { StringEquals = {
        "aws:PrincipalTag/eks-cluster-name"           = var.management_cluster_name
        "aws:PrincipalTag/kubernetes-namespace"       = "iris-platform"
        "aws:PrincipalTag/kubernetes-service-account" = "iris-onprem-ecr-renewer-28"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "onprem_ecr_pull" {
  name = "pull-service-images"
  role = aws_iam_role.onprem_ecr_pull.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetAuthorizationToken has no resource-level permissions.
        Sid      = "EcrAuthorizationToken"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid      = "PullServiceImages"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = local.service_repository_arns
      },
    ]
  })
}

# Only this role, and only AssumeRole: the API never pulls images itself.
resource "aws_iam_role_policy" "control_api_onprem_ecr_pull" {
  name = "assume-onprem-ecr-pull"
  role = aws_iam_role.control_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AssumeOnpremEcrPull"
      Effect   = "Allow"
      Action   = ["sts:AssumeRole"]
      Resource = aws_iam_role.onprem_ecr_pull.arn
    }]
  })
}

output "onprem_ecr_pull_role_arn" {
  description = "Role the Control API assumes for on-prem server ECR pull tokens (WAS ONPREM_ECR_PULL_ROLE_ARN)"
  value       = aws_iam_role.onprem_ecr_pull.arn
}
