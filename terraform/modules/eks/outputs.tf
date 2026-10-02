output "name" { value = aws_eks_cluster.this.name }
output "arn" { value = aws_eks_cluster.this.arn }
output "endpoint" { value = aws_eks_cluster.this.endpoint }
output "certificate_authority_data" { value = aws_eks_cluster.this.certificate_authority[0].data }
output "cluster_security_group_id" { value = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id }
output "api_security_group_id" { value = aws_security_group.api.id }
output "node_security_group_id" { value = aws_security_group.node.id }
output "node_group_names_by_az" { value = { for az, group in aws_eks_node_group.az : az => group.node_group_name } }
output "load_balancer_controller_role_arn" { value = aws_iam_role.lbc.arn }
