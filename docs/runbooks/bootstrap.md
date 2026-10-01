# bootstrap

1. AWS 로그인 방식을 준비하고 `aws sts get-caller-identity`로 계정 ID를 확인합니다.
2. 각 root의 `.example` 설정을 로컬 파일로 복사하고 실제 계정·리전·버킷명으로 바꿉니다.
3. 구현된 bootstrap S3 구성과 버킷명을 확인합니다. 버킷명은 `<project>-tfstate-<aws_account_id>-<aws_region>`이며 backend.hcl에도 같은 이름을 입력합니다.
4. bootstrap은 backend.tf의 S3 선언을 주석 상태로 두고 로컬 init/plan/apply합니다. 최초 계획은 버킷·versioning·암호화·public access block 4개 생성입니다. prevent_destroy를 적용하고 force_destroy는 false로 유지합니다.
5. 버킷 생성 후 bootstrap의 S3 선언을 활성화하고 `terraform init -migrate-state -backend-config=backend.hcl`로 state를 이전합니다. 로컬 state를 Git에 올리지 않습니다.
6. account의 GitHub OIDC 역할과 foundation 배포 권한을 관리자가 적용하고 [CI runbook](terraform-ci.md)에 따라 main 자동 apply를 연결합니다. 기존 IAM은 import합니다. CI는 bootstrap 다음에 구현된 foundation 빌드 자원을 적용합니다. 기존 foundation state가 있다면 CI와 같은 key로 먼저 이전합니다. 팀 IAM 정책과 foundation의 VPC·ECR·공통 배포 역할은 후속 구현합니다.
7. management와 workload는 리소스·입력·IAM을 준비하고 `.scaffold`를 제거한 후 실행합니다. CI에서는 foundation 다음에 순서대로 적용합니다. foundation만 참조하고 서로의 state는 참조하지 않습니다.
8. EKS kubeconfig를 생성하며 clusters의 kubeContext 별칭을 지정합니다. 계정·context를 재확인합니다.
9. baseline·외부 addon을 구현한 뒤 `make bootstrap CLUSTER=...`를 사용합니다.
10. IAM/RBAC·스토리지·Ingress·image pull을 확인합니다.

현재 state용 S3 버킷, GitHub CI OIDC 역할, foundation의 CodeBuild·빌드 입력 S3·로그·빌드 역할을 구현했습니다. 팀 IAM·네트워크·EKS 자원과 클러스터 bootstrap은 미구현입니다.
S3 state 파일에는 Get/Put, `.tflock`에는 Get/Put/Delete 권한이 필요하며 CI plan 역할에도 잠금 권한을 부여합니다.
공식 [S3 backend 문서](https://developer.hashicorp.com/terraform/language/backend/s3)를 참고합니다.
