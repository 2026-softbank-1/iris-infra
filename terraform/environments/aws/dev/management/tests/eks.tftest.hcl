mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
  mock_resource "aws_iam_policy" { defaults = { arn = "arn:aws:iam::123456789012:policy/mock-policy" } }
  mock_resource "aws_iam_openid_connect_provider" { defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/MOCK" } }
  mock_resource "aws_launch_template" { defaults = { latest_version = 1, id = "lt-0123456789abcdef0" } }
  mock_resource "aws_eks_cluster" { defaults = {
    arn                   = "arn:aws:eks:ap-northeast-2:123456789012:cluster/mock"
    endpoint              = "https://example.eks.amazonaws.com"
    certificate_authority = [{ data = "Y2VydA==" }]
    identity              = [{ oidc = [{ issuer = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/MOCK" }] }]
  } }
}

override_data {
  target = data.terraform_remote_state.foundation
  values = { outputs = {
    vpc_id                                  = "vpc-0123456789abcdef0"
    management_cluster_name                 = "iris-dev-management"
    workload_cluster_name                   = "iris-dev-workload"
    management_subnet_ids_by_az             = { ap-northeast-2a = "subnet-0123456789abcdef0", ap-northeast-2c = "subnet-0123456789abcdef1" }
    workload_subnet_ids_by_az               = { ap-northeast-2a = "subnet-0123456789abcdef2", ap-northeast-2c = "subnet-0123456789abcdef3" }
    management_api_source_security_group_id = "sg-0123456789abcdef1"
    workload_api_target_security_group_id   = "sg-0123456789abcdef2"
    ssm_bridge_instance_id                  = "i-0123456789abcdef0"
    ssm_bridge_security_group_id            = "sg-0123456789abcdef3"
    argocd_management_role_arn              = "arn:aws:iam::123456789012:role/argocd"
    argocd_deploy_role_arns                 = { management = "arn:aws:iam::123456789012:role/argocd-management-deploy", workload = "arn:aws:iam::123456789012:role/argocd-workload-deploy" }
    deploy_worker_role_arn                  = "arn:aws:iam::123456789012:role/deploy-worker"
    build_worker_role_arn                   = "arn:aws:iam::123456789012:role/build-worker"
  } }
}
variables {
  aws_account_id                     = "123456789012"
  operator_principal_arn             = "arn:aws:iam::123456789012:role/operator"
  additional_operator_principal_arns = ["arn:aws:iam::123456789012:user/second-operator"]
}
run "foundation_contract" {
  command = apply
  assert {
    condition     = output.target.id == "aws-dev-management" && output.target.subnet_ids_by_az == data.terraform_remote_state.foundation.outputs.management_subnet_ids_by_az && output.target.argocd_role_arn == data.terraform_remote_state.foundation.outputs.argocd_deploy_role_arns["management"] && output.target.ssm_bridge_instance_id == "i-0123456789abcdef0" && output.target.additional_operator_principal_arns == var.additional_operator_principal_arns
    error_message = "Consume only the correct foundation target contract, without cross-root state."
  }
}

run "platform_pod_identity" {
  command = plan
  assert {
    condition     = aws_eks_pod_identity_association.deploy_worker.namespace == "iris-platform" && aws_eks_pod_identity_association.deploy_worker.service_account == "deploy-worker" && aws_eks_pod_identity_association.deploy_worker.role_arn == data.terraform_remote_state.foundation.outputs.deploy_worker_role_arn && aws_eks_pod_identity_association.build_worker.service_account == "build-worker"
    error_message = "Management must bind separate Build/Deploy service accounts to their own foundation roles."
  }
}
