locals {
  state_bucket = var.state_bucket_name != "" ? var.state_bucket_name : "${var.project}-tfstate-${var.aws_account_id}-${var.aws_region}"
  tags         = { Project = var.project, Environment = var.environment, ManagedBy = "Terraform", Component = "eks" }
}
data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket              = local.state_bucket
    key                 = "aws/dev/foundation/terraform.tfstate"
    region              = var.aws_region
    encrypt             = true
    allowed_account_ids = [var.aws_account_id]
  }
}
module "eks" {
  source                             = "../../../../modules/eks"
  aws_account_id                     = var.aws_account_id
  aws_region                         = var.aws_region
  cluster_name                       = data.terraform_remote_state.foundation.outputs.management_cluster_name
  vpc_id                             = data.terraform_remote_state.foundation.outputs.vpc_id
  subnet_ids_by_az                   = data.terraform_remote_state.foundation.outputs.management_subnet_ids_by_az
  service_cidr                       = "172.20.0.0/16"
  operator_principal_arn             = var.operator_principal_arn
  argocd_deploy_role_arn             = data.terraform_remote_state.foundation.outputs.argocd_deploy_role_arns["management"]
  bridge_security_group_id           = data.terraform_remote_state.foundation.outputs.ssm_bridge_security_group_id
  additional_node_security_group_ids = [data.terraform_remote_state.foundation.outputs.management_api_source_security_group_id]
  tags                               = local.tags
}
