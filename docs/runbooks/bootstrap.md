# bootstrap

1. AWS 로그인 방식을 준비하고 `aws sts get-caller-identity`로 계정 ID를 확인합니다.
2. 각 root의 `.example` 설정을 로컬 파일로 복사하고 실제 계정·리전·버킷명으로 바꿉니다.
3. bootstrap S3 리소스를 구현합니다. versioning·암호화·public access block·prevent_destroy를 설정합니다.
4. bootstrap은 backend.tf의 S3 선언을 주석 상태로 두고 로컬 init/plan/apply합니다. 구현 검증 후 `.scaffold`를 제거합니다.
5. 버킷 생성 후 bootstrap의 S3 선언을 활성화하고 `terraform init -migrate-state -backend-config=backend.hcl`로 state를 이전합니다. 로컬 state를 Git에 올리지 않습니다.
6. account를 구현하며 기존 IAM은 import합니다. foundation에서 VPC·ECR·공통 역할을 생성합니다.
7. management와 workload를 각각 실행합니다. foundation만 참조하고 서로의 state는 참조하지 않습니다.
8. EKS kubeconfig를 생성하며 clusters의 kubeContext 별칭을 지정합니다. 계정·context를 재확인합니다.
9. baseline·외부 addon을 구현한 뒤 `make bootstrap CLUSTER=...`를 사용합니다.
10. IAM/RBAC·스토리지·Ingress·image pull을 확인합니다.

현재 리소스와 클러스터 bootstrap은 미구현입니다. 위 절차는 구현 순서입니다.
S3 state 파일에는 Get/Put, `.tflock`에는 Get/Put/Delete 권한이 필요하며 CI plan 역할에도 잠금 권한을 부여합니다.
공식 [S3 backend 문서](https://developer.hashicorp.com/terraform/language/backend/s3)를 참고합니다.
