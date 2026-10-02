output "target" {
  description = "Infrastructure-only target contract. No credentials or complete state."
  value = {
    id                                      = "aws-dev-workload"
    kube_context                            = "iris-dev-workload"
    operator_principal_arn                  = var.operator_principal_arn
    additional_operator_principal_arns      = var.additional_operator_principal_arns
    load_balancer_controller_role_arn       = module.eks.load_balancer_controller_role_arn
    management_api_source_security_group_id = data.terraform_remote_state.foundation.outputs.management_api_source_security_group_id
    workload_api_target_security_group_id   = data.terraform_remote_state.foundation.outputs.workload_api_target_security_group_id
    account_id                              = var.aws_account_id
    region                                  = var.aws_region
    name                                    = module.eks.name
    arn                                     = module.eks.arn
    endpoint                                = module.eks.endpoint
    ca_data                                 = module.eks.certificate_authority_data
    vpc_id                                  = data.terraform_remote_state.foundation.outputs.vpc_id
    subnet_ids_by_az                        = data.terraform_remote_state.foundation.outputs.workload_subnet_ids_by_az
    cluster_security_group_id               = module.eks.cluster_security_group_id
    api_security_group_id                   = module.eks.api_security_group_id
    node_security_group_id                  = module.eks.node_security_group_id
    node_group_names_by_az                  = module.eks.node_group_names_by_az
    argocd_role_arn                         = data.terraform_remote_state.foundation.outputs.argocd_deploy_role_arns["workload"]
    ssm_bridge_instance_id                  = data.terraform_remote_state.foundation.outputs.ssm_bridge_instance_id
    ssm_bridge_security_group_id            = data.terraform_remote_state.foundation.outputs.ssm_bridge_security_group_id
    api_port                                = 11443
  }
}
