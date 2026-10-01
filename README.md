# iris-infra

Iris 플랫폼의 AWS 자원, Kubernetes 공통 설정, Helm 차트와 운영 절차를 관리하는 저장소입니다.
서비스 소스와 API / Worker의 배포 오케스트레이션은 각 서비스·백엔드 저장소에서 관리합니다.

현재는 **bootstrap S3 버킷·GitHub CI 인증·foundation 빌드 자원·공유 VPC 네트워크 구현**이 준비되어 있습니다.
`terraform/bootstrap/aws`는 state 버킷·versioning·암호화·public access block을 관리합니다.
`terraform/account/aws`는 GitHub OIDC와 bootstrap 자동 배포 역할을 관리합니다.
foundation은 VPC·서브넷·IGW·NAT·라우팅·관리→앱 API 접근용 SG를 정의하며, 네트워크 CI 권한은 account에서 관리자가 먼저 적용합니다.
EKS, Helm manifest, 팀 IAM과 운영 스크립트의 실제 배포 동작은 아직 구현하지 않았습니다.
`.scaffold`가 있는 Terraform stack은 팀 명령에서 plan/apply를 차단합니다.
구현·검증 후 해당 표시를 제거하고 아래 순서로 진행합니다.

## 구성

```text
terraform/
  bootstrap/aws/                    # Terraform state S3
  account/aws/                      # 계정 IAM, GitHub OIDC / CI 역할
  environments/aws/dev/
    foundation/                     # VPC, ECR, 공통 workload IAM
    management/                     # 관리 EKS, Pod Identity
    workload/                       # 앱 EKS, Worker Access Entry
  modules/eks/                      # 두 EKS의 재사용 구성
helm/charts/
  cluster-baseline/                  # Namespace, RBAC, Quota
  iris-platform/                     # API, Worker, Agent, 플랫폼 PostgreSQL
  iris-service/                      # 공통 앱 Deployment, Service, Ingress
clusters/
  aws-dev-management/
  aws-dev-workload/
  local-workload/
local/                              # k3d, 이미지 import, 로컬 접속
contracts/                          # 백엔드·CLI 연동 규격 초안
examples/                           # AWS·로컬 values와 BuildKit 샘플 위치
scripts/                            # 검증과 팀 명령
docs/                              # 아키텍처, 결정 기록, runbook
.github/workflows/                  # PR 검증, main bootstrap 자동 apply
```

## 도구

- Terraform: `.terraform-version`의 **1.16.4** (root의 최소 조건은 1.10).
- AWS provider: 6.x. 첫 init 후 각 root의 `.terraform.lock.hcl`을 commit하여 정확한 버전을 고정합니다.
- Helm, AWS CLI v2, kubectl, k3d, Docker: 해당 기능 구현 시 클러스터 버전과 호환되는 버전을 확정합니다.
- Bash, Make, Python 3: 현재 scaffold 검증과 공통 명령에 사용합니다.

Terraform 고정 버전은 [공식 릴리스](https://releases.hashicorp.com/terraform/1.16.4/)를 기준으로 했습니다.

## 시작

```bash
make help
make scaffold-check
make tf-fmt-check
# 지정 Terraform 버전과 provider 다운로드가 가능할 때:
make tf-check
# Helm 설치 후: 현재는 빈 차트의 기본 형식만 검증합니다.
make helm-check
```

AWS 계정 ID, state 버킷, 리전, EKS·노드 설정, 이미지와 도메인을 팀 값으로 확정합니다.
`terraform.tfvars.example`과 `backend.hcl.example`을 각 stack에서 복사하여 로컬 설정을 만듭니다.
실제 설정·state·plan·Secret은 Git에 올리지 않습니다. 예시의 `000000000000`은 실제 계정 ID로 교체합니다.

구현 순서: **bootstrap → account / foundation → management / workload → baseline·애드온 → 서비스 샘플 → 플랫폼·빌드 연동 → 로컬 CLI → OCI 릴리스**.
bootstrap은 로컬 state로 S3를 만든 뒤 backend를 활성화하고 state를 이전합니다.

```bash
# 리소스 구현과 설정 완료 후 사용할 인터페이스
make tf-init STACK=aws/dev/foundation
make tf-plan STACK=aws/dev/foundation
make tf-apply STACK=aws/dev/foundation
make bootstrap CLUSTER=aws-dev-management
make smoke-test TARGET=local-workload
```

bootstrap / smoke-test / export-targets는 현재 미구현 안내와 함께 종료합니다.
PR에서는 fmt/init/validate와 mock IAM·네트워크 테스트를 실행합니다.
main에서는 검증 성공 후 OIDC 인증과 bootstrap → foundation 자동 apply를 실행합니다.
최초 역할 생성과 GitHub 변수 설정은 [Terraform CI runbook](docs/runbooks/terraform-ci.md)에 있습니다.
네트워크 입력·경로·후속 출력은 [foundation README](terraform/environments/aws/dev/foundation/README.md)에 있습니다.
후속 EKS stack과 OCI push workflow는 해당 리소스 구현 후 연결합니다.

## 문서

- [원본 설계안](infra-repository-design.md)
- [아키텍처와 책임](docs/architecture.md)
- [설계 결정](docs/decisions/README.md)
- [계정 준비와 bootstrap](docs/runbooks/bootstrap.md)
- [Terraform CI와 main 자동 배포](docs/runbooks/terraform-ci.md)
- [플랫폼 배포](docs/runbooks/deploy-platform.md)
- [문제 확인](docs/runbooks/troubleshooting.md)
- [철거](docs/runbooks/teardown.md)
- [배포·타겟·빌드 계약](contracts/README.md)
