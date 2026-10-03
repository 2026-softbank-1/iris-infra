# 계정·state와 최초 배포

기존 bootstrap/account/foundation state를 먼저 확인합니다. 현재 코드가 구현된 것은 실제 적용 성공을 뜻하지 않습니다. account 관리자 작업·main merge·Helm/Kubernetes 배포는 각 실행 시 운영자가 승인한 범위로 진행합니다.

1. `aws sts get-caller-identity`로 실제 계정과 운영자 IAM principal을 확인합니다. Free 계정 EKS 서비스 허용/크레딧/만료/quota를 확인하고 Paid 전환은 자동으로 하지 않습니다.
2. 각 root의 `.example`을 로컬 backend.hcl/tfvars로 복사하고 실제 계정·리전·버킷을 맞춥니다. 실제 설정/state/plan/credentials는 Git에 넣지 않습니다.
3. state 버킷이 없을 때만 최초 bootstrap을 로컬 backend로 plan/apply한 후 기존 S3 선언과 `init -migrate-state`를 사용합니다. 버킷은 versioning/encryption/public block/prevent_destroy/force_destroy=false를 유지합니다. 이미 존재하면 생성 과정을 반복하지 않습니다.
4. 기존 foundation state가 `aws/dev/foundation/terraform.tfstate`에 있는지 확인합니다. 다른 위치의 state는 백업·내용·주소 확인 후 이전하고 외부 생성 자원은 검토하여 import합니다.
5. 관리자 account plan/apply로 기존 OIDC/build/ECR/network와 새 EKS/runtime IAM/compute/bridge 권한, CI session7200을 선적용합니다. [CI runbook](terraform-ci.md)의 variables/subject/input을 준비합니다.
6. CIDR/AZ/기존 자원 보존 plan과 비용·철거 시각을 검토하고 **사용자가 main merge**합니다. CI가 bootstrap→foundation→management→workload를 자동 apply합니다. 두 EKS는 foundation만 읽습니다.
7. [private API runbook](eks-access.md)으로 output export·두 SSM 터널·TLS/RBAC/node/CNI를 확인합니다.
8. 운영자가 local Helm/kubectl/plugin과 read-only private Git credential, 최신 main checkout을 준비하여 `make bootstrap CLUSTER=aws-dev-management`를 실행합니다. Argo 설치와 root/8addon sync가 완료되어야 합니다.
9. 두 target의 읽기 전용 smoke를 실행합니다. 쓰기/PodIdentity/NetworkPolicy/PVC/기존 ECR digest/노드 drain은 명시한 opt-in exercise로 추가 확인합니다.
10. 실제 iris-platform/사용자 앱/DB/Ingress는 후속 구현입니다. 관측 singleton EBS는 HA가 아니며 [철거](teardown.md)를 별도로 수행합니다.

S3 state에는 Get/Put, `.tflock`에는 Get/Put/Delete가 필요합니다. account는 CI 대상이 아니며 전체 state 읽기를 서비스·GitOps에 주지 않습니다. [S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3)를 참고합니다.
