resource "aws_eks_pod_identity_association" "argocd" {
  for_each        = toset(["argocd-server", "argocd-application-controller", "argocd-applicationset-controller"])
  cluster_name    = module.eks.name
  namespace       = "argocd"
  service_account = each.value
  role_arn        = data.terraform_remote_state.foundation.outputs.argocd_management_role_arn
  depends_on      = [module.eks]
}
resource "aws_eks_pod_identity_association" "build_worker" {
  cluster_name    = module.eks.name
  namespace       = "iris-platform"
  service_account = "build-worker"
  role_arn        = data.terraform_remote_state.foundation.outputs.build_worker_role_arn
  depends_on      = [module.eks]
}

# Control API reads build logs only. The service account is the chart's `{release}-api`.
resource "aws_eks_pod_identity_association" "control_api" {
  cluster_name    = module.eks.name
  namespace       = "iris-platform"
  service_account = "iris-platform-api"
  role_arn        = data.terraform_remote_state.foundation.outputs.control_api_role_arn
  depends_on      = [module.eks]
}

# Console Gateway signs a caller identity for the workload EKS Access Entry. The service account is the
# chart's fixed `console-gateway`; its role has no AWS permission.
resource "aws_eks_pod_identity_association" "console_gateway" {
  cluster_name    = module.eks.name
  namespace       = "iris-platform"
  service_account = "console-gateway"
  role_arn        = data.terraform_remote_state.foundation.outputs.console_gateway_role_arn
  depends_on      = [module.eks]
}

resource "aws_eks_pod_identity_association" "onprem_ecr_renewer" {
  cluster_name    = module.eks.name
  namespace       = "iris-platform"
  service_account = "iris-onprem-ecr-renewer"
  role_arn        = data.terraform_remote_state.foundation.outputs.onprem_ecr_renewer_role_arn
  depends_on      = [module.eks]
}

resource "aws_eks_pod_identity_association" "deploy_worker" {
  cluster_name    = module.eks.name
  namespace       = "iris-platform"
  service_account = "deploy-worker"
  role_arn        = data.terraform_remote_state.foundation.outputs.deploy_worker_role_arn
  depends_on      = [module.eks]
}

resource "aws_eks_pod_identity_association" "loki" {
  cluster_name    = module.eks.name
  namespace       = "observability"
  service_account = "loki"
  role_arn        = data.terraform_remote_state.foundation.outputs.loki_role_arn
  depends_on      = [module.eks]
}

resource "aws_eks_pod_identity_association" "alb_log_collector" {
  cluster_name    = module.eks.name
  namespace       = "observability"
  service_account = "alb-log-collector"
  role_arn        = data.terraform_remote_state.foundation.outputs.alb_access_logs.collector_role_arn
  depends_on      = [module.eks]
}
