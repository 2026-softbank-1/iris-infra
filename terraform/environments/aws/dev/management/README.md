# 관리 EKS

private EKS1.35와 AZ별 m7i-flex.large 노드 1대씩을 공통 eks module로 생성합니다. foundation의 VPC/subnet/name/SSM/Argo IAM만 참조하며 상대 EKS의 state를 읽지 않습니다. service CIDR은 172.20.0.0/16입니다.

관리 root는 Argo 3개 SA, build-worker·deploy-worker SA, `observability/loki` SA의 Pod Identity를 연결합니다. 실제 Worker Pod/RBAC는 후속 범위입니다.

독립 state key는 `aws/dev/management/terraform.tfstate`입니다. 실제 계정·리전·버킷과 필수 `operator_principal_arn`을 로컬 example/backend로 준비합니다. IAM user/role ARN을 사용하며 STS 세션 ARN은 거부합니다. 운영자와 Argo deploy role은 cluster-admin입니다.

`.scaffold`를 제거하여 main CI 자동 apply 대상입니다. account IAM/session 선적용과 GitHub `EKS_OPERATOR_PRINCIPAL_ARN` 설정 뒤 사용자가 merge합니다. `make tf-init/tf-plan/tf-apply STACK=aws/dev/management`는 별도 배포 시 실제 AWS 작업입니다.

`target` 출력과 `make export-targets`는 비밀값 없는 infrastructure contract만 전달합니다. [API 접근/설치/검증](../../../../../docs/runbooks/eks-access.md), [target 계약](../../../../../contracts/target.md)을 참고합니다.
