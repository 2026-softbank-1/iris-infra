# EKS infrastructure target 계약 v1

Terraform `management/workload`의 `target` 출력과 `scripts/export-targets.sh`가 사용하는 운영 계약입니다. 플랫폼 등록 인터페이스/ConfigMap 연동은 아직 합의 전이며 이 계약만으로 Worker 배포 권한을 제공하지 않습니다. Git의 `clusters/<id>/cluster.yaml`은 target metadata이고 Kubernetes manifest가 아닙니다.

`.generated/targets.json`은 `{schema_version:1, targets:{aws-dev-management:{...}, aws-dev-workload:{...}}}`이며 0600 권한/ignored 로컬 파일입니다. endpoint/CA/role ARN/SG/subnet/name 등 필요한 출력만 포함하고 전체 state·token·Git credential·kubeconfig는 포함하지 않습니다. CA는 PEM의 base64 문자열입니다.

| 필드 | 의미 |
| --- | --- |
| id / account_id / region | 고정 AWS target, 명시 계정, 서울 리전 |
| name / arn / endpoint / ca_data | 실제 EKS 이름·cluster ARN·private API URL·TLS CA |
| kube_context / api_port | iris-dev-management:10443 또는 iris-dev-workload:11443 |
| vpc_id / subnet_ids_by_az | 공유 VPC와 target별2a/c private subnet map |
| cluster_security_group_id / api_security_group_id / node_security_group_id | EKS/node/bridge API 연결 SG |
| management_api_source_security_group_id / workload_api_target_security_group_id | 관리→앱443 SG 참조 계약 |
| node_group_names_by_az | AZ별 고정1노드 MNG 이름 |
| ssm_bridge_instance_id / ssm_bridge_security_group_id | private API tunnel 경로 |
| operator_principal_arn | 실제 caller와 일치할 IAM user/role Access Entry |
| additional_operator_principal_arns | 같은 cluster-admin Access Entry를 받는 추가 운영자 목록(기본 `[]`) |
| argocd_role_arn / load_balancer_controller_role_arn | target deploy/LBC identity |
| argocd_management_role_arn | management에만 포함하는3SA Pod Identity role |

export/접근 시 caller 계정·ARN을 검사하고 preflight에서 AWS 실제 cluster endpoint/CA/VPC/private 설정·subnet/SG/SSM을 다시 확인합니다. 변경된 cluster를 오래된 target으로 접근하면 실패합니다. TLS는 localhost 포워딩에도 원래 hostname과 CA를 사용합니다. 운영자 RBAC는 bootstrap/smoke에서 실제 API로 확인합니다.

구조는 [target.schema.json](target.schema.json), 실행은 [private API runbook](../docs/runbooks/eks-access.md)을 참고합니다. 플랫폼 target 등록 방식·credential 참조·사용자 앱 ApplicationSet의 배포 경계는 후속 합의입니다. Deploy Worker는 ADR 0002에 따라 values를 Git에 커밋하며 앱 EKS API 권한을 갖지 않습니다.
