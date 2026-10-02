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
