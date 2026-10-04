# GCP 배포 파이프라인 준비와 활성화

GCP 계정 없이 PR/main에서 Terraform 형식·backend 없는 validate·mock test·Helm 렌더링·CI gate와 이미지 빌드를 검증한다. `.github/workflows/gcp-terraform.yml`은 `GCP_TERRAFORM_DEPLOY_ENABLED=true`가 되기 전 자동 인증/apply를 하지 않는다. `.github/workflows/gcp-ecr-credentials.yml`도 `GCP_ECR_PUBLISH_ENABLED=true` 전에는 AWS 인증/게시를 하지 않는다. 두 flag는 **repository variables**에 설정한다. 계정 미준비 상태에서는 입력 예시의 빈 값과 offline readiness 실패가 정상이다.

## 계정 없이 가능한 검증

```bash
make scaffold-check tf-check tf-test helm-check gcp-ci-test
python3 scripts/tests/test-gcp.py
python3 scripts/tests/test-eks-ops.py
make gcp-readiness
docker build --platform linux/amd64 --tag iris-gcp-credentials-check runtime/gcp-ecr-credentials
```

Terraform 1.16.4 (`.terraform-version`), Helm 3.19.1, Python/PyYAML 6.0.3, provider 다운로드 네트워크와 Docker daemon이 필요하다. readiness는 클라우드를 호출하지 않고 부족한 필드를 표시하며 exit 2로 종료한다. 다른 검사는 실제 billing, IAM, quota, GKE/cert/Gateway 성공을 보장하지 않는다. CI job의 실제 실행도 merge/main push 또는 명시적 workflow 실행 승인 후에 확인한다.

## 계정이 준비된 뒤의 순서

1. 관리자가 GCP 프로젝트를 생성하고 billing을 연결한다. 서울 `asia-northeast3`/`asia-northeast3-a`, `*.gcp.likelion.uk`를 사용한다. Storage/Service Usage/Resource Manager API enable 권한을 확보한다. 초기 Service Usage/Resource Manager API가 꺼져 있으면 콘솔 또는 명시적 관리자 `gcloud services enable`로 준비한다. 프로젝트/billing은 Terraform이 만들지 않는다.
2. [GCP workload runbook](gcp-workload.md)의 local bootstrap → state bucket 생성 → 명시적 GCS state migration 절차를 따른다. bootstrap prefix=`gcp/bootstrap`. 실제 backend/tfvars/state는 Git에 넣지 않는다.
3. `terraform/account/gcp/{backend.hcl,terraform.tfvars}`를 example에서 만들고 기존 bucket, 프로젝트, GitHub 숫자 repository/owner ID를 입력한다. `make gcp-github-discover`는 `gh` 인증을 사용한 읽기 전용 API 조회이며 변수나 권한을 쓰지 않는다. AWS OIDC customization의 실제 subject 형식도 확인한다.
4. 관리자가 `GCP_PROJECT_ID=... make tf-init/tf-plan/tf-apply STACK=account/gcp`를 순서대로 실행한다. 일반 Terraform interactive approval을 유지한다. account prefix=`gcp/account`; bootstrap/account는 자동 workflow 적용 대상이 아니다. 기존 GCP state에 IAM API가 이미 등록되어 있다면 중단하고 state 이전 계획을 별도 리뷰한다.
5. `GCP_PROJECT_ID=... make gcp-ci-config`로 account의 **비밀이 아닌 ci output만** `.generated/gcp-ci.json`에 추출한다. 관리 EKS의 public OIDC issuer를 추가하고 `make gcp-readiness GCP_CI_CONFIG=.generated/gcp-ci.json`을 실행한다. `.generated`는 private 권한 및 Git ignore로 보호한다. 아래 repository/environment variables를 운영자가 설정한다.
6. GitHub `gcp-dev` environment를 main만 허용하도록 설정하고 protected main/필수 검증 상태를 적용한다. 사용 중인 GitHub 요금제에서 environment protection/required reviewers가 지원되면 함께 설정한다. 지원 여부를 사전 확인하며, 없는 보호 기능을 활성화 조건으로 가정하지 않는다. 실제 숫자 ID, workflow_ref, event/ref, environment와 OIDC claim, SA impersonation 및 IAM 권한을 검증한다.
7. 최신 main에서 workflow_dispatch `action=plan`을 실행한다. manual plan은 flag가 false여도 명시적으로 인증한다. missing metadata, project/SA/WIF/bucket 불일치, stale SHA, verify 실패는 apply 전에 차단한다. 실제 ADC 재발급/refresh가 provider와 GCS에서 작동하는지 확인한다. `make gcp-online-readiness GCP_CI_CONFIG=.generated/gcp-ci.json`은 관리자 인증으로 프로젝트/billing/초기 APIs/quota를 **읽기만** 한다. Compute API가 아직 꺼져 있으면 quota 검사는 보류하고 활성화 후 다시 실행한다.
8. plan의 비용과 자원을 검토한 뒤 `GCP_TERRAFORM_DEPLOY_ENABLED=true`를 설정하고 main의 manual `action=apply`를 실행한다. 그 이후 main runtime 변경은 자동 apply 대상이다. account/bootstrap/docs/mock test 변경만으로는 GCP 자동 apply를 하지 않는다. workload 비용은 GKE control plane, 1–2개 e2-standard-2 노드, NAT, public IP/Gateway 및 로그 등을 포함한다.
9. workload output의 ECR GSA numeric unique ID를 가져와 관리자가 `aws/dev/gcp-access`를 적용한다. publisher가 필요하면 `enable_github_publisher=true`와 **실제** `github_oidc_subject_prefix`를 설정한다. `account/aws`가 기존 GitHub OIDC provider를 먼저 소유해야 한다. 이 stack은 기존 AWS 자동 apply에서도 제외한다. 기존 pull role은 읽기만 허용하며 publisher는 한 ECR repository만 게시할 수 있다.
10. publisher role ARN/repository 변수를 설정하고 `GCP_ECR_PUBLISH_ENABLED=true` 이후 최신 main에서 manual publish를 실행한다. 반드시 같은 SHA의 cloud-free tests와 linux/amd64 Docker build가 성공해야 한다. 출력된 `repository@sha256:...`를 보관한다. 동일 SHA tag는 revision label을 검사하고 재사용하며 삭제/덮어쓰기를 하지 않는다.
11. iris-service 0.10.0 등 필요한 chart tag 게시를 별도 승인/실행한다. workflow는 chart tag를 게시하거나 GitOps 설정을 쓰지 않는다. `make gcp-bootstrap GCP_ECR_IMAGE=repository@sha256:... AWS_ACCOUNT_ID=...`에서 digest를 사용한다. DNS authorization CNAME → certificate ACTIVE → wildcard A 레코드의 순서와 GCP bootstrap/키 backup을 [workload runbook](gcp-workload.md)대로 진행한다.
12. 준비된 GCP target을 AWS private 관리 클러스터 Argo에 등록하고 opt-in GitOps를 활성화한다. Gateway HTTPS, namespace 격리, ECR rotation, 재시작 후 image pull, Sealed Secrets backup/restore와 AWS 터널 없이 GCP 서비스가 응답하는지 live smoke-test로 확인한다. 서비스 배포 target 선택은 iris-was Worker의 별도 구현과 통합 검증이 필요하다.

## GitHub 변수

flag는 repository scope에 두며 기본값은 false다. GCP nonsecret metadata는 repository 또는 `gcp-dev` environment scope를 사용할 수 있다. AWS publisher job은 main-ref subject를 유지하기 위해 environment를 사용하지 않는다.

| 변수 | 값/출처 |
|---|---|
| `GCP_TERRAFORM_DEPLOY_ENABLED` | 기본 false; 검토 후 true |
| `GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER` | account `ci` output |
| `GCP_STATE_BUCKET` | bootstrap bucket / account output |
| `GCP_TERRAFORM_SERVICE_ACCOUNT` | `iris-gcp-terraform@PROJECT.iam.gserviceaccount.com` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/NUMBER/locations/global/workloadIdentityPools/iris-github-ci/providers/github` |
| `GCP_GITHUB_REPOSITORY_ID`, `GCP_GITHUB_OWNER_ID` | 실제 숫자 GitHub IDs |
| `GCP_MANAGEMENT_OIDC_ISSUER` | AWS management EKS public OIDC issuer |
| `GCP_BASE_DOMAIN` | 기본 `gcp.likelion.uk` |
| `GCP_ECR_PUBLISH_ENABLED` | 기본 false; 역할 적용 후 true |
| `AWS_ACCOUNT_ID` | 실제 ECR AWS account |
| `GCP_ECR_PUBLISH_ROLE_ARN` | gcp-access `github_publisher_role_arn` output |
| `GCP_ECR_REPOSITORY` | gcp-access `credentials_repository` output |

region/zone/workload state prefix는 CI에서 고정하며 환경 변수로 다른 대상에 적용할 수 없다. source SHA는 verify checkout와 일치해야 하며 credentials 발급 전 및 write 직전에 GitHub API로 최신 main SHA를 확인한다. Terraform과 이미지 workflow는 각각 state/repository 단위 concurrency를 사용하며 실행 중 apply를 새 push가 취소하지 않는다.

## 권한 경계와 실패 처리

GitHub WIF는 숫자 repository/owner ID, 정확한 main workflow_ref, main ref와 push/workflow_dispatch를 제한한다. Argo WIF와 pool을 나눈다. 인증 action은 고정 commit을 사용하며 refresh 가능한 GitHub OIDC URL → STS → SA impersonation ADC를 만든다. [고정 action의 credential_source 구현](https://github.com/google-github-actions/auth/blob/7c6bc770dae815cd3e89ee6cdf493a5fab2cc093/src/client/workload_identity_federation.ts)을 확인했으며 CI preflight에서 URL/query audience, header, format, STS와 impersonation endpoint를 검사한다. 고정 access token/장기 SA JSON key를 사용하지 않는다. 120분 job에서 실제 refresh 및 오래 걸리는 apply는 초기 활성화 시 확인해야 한다.

CI의 `projectIamAdmin`은 자기 권한을 추가할 수 있고 SA/WIF 관리 권한은 CI 자신의 신뢰 설정을 바꿀 수 있다. **별도 account state와 prefix 제한은 침해된 CI에 대한 보안 격리가 아니다.** 정상 workflow는 workload root만 복사하며 workload prefix의 object만 직접 읽고 쓴다. bucket metadata/list 권한은 다른 state object 이름을 노출할 수 있다. protected main, 정확한 workflow trust와 실제 IAM 검증이 이 권한을 맡기는 경계다. 기본 Owner/Editor, Secret Manager version access는 부여하지 않는다.

Terraform은 private 임시 디렉터리와 0600 saved plan을 사용한다. 로컬 tfvars/backend/state/cache/override를 재사용하지 않고 환경의 TF CLI override/log 설정도 제거한다. plan/show/cloud raw output은 캡처하고 검증된 target과 resource address/action만 표시한다. plan/state/credentials artifact를 upload하지 않는다. 삭제·교체는 manual plan에서도 차단하며 별도 관리자 변경 계획을 요구한다. GCS lock timeout은 5분이다.

실패/취소 시 child process와 임시 plan을 정리하고 auth action post에서 ADC 파일을 제거한다. hard kill/runner 유실은 post cleanup을 보장하지 못하므로 GitHub hosted ephemeral runner를 사용한다. 부분 적용된 remote state는 보존하고 원인을 해결한 뒤 **새 plan**으로 재시도한다. 자동 destroy, state 삭제, force-unlock 또는 saved plan 재사용을 하지 않는다. 중단하려면 flag=false로 이후 자동 실행을 막는다. 이미 실행 중인 apply는 별도로 상태를 확인하고 취소/복구를 판단한다. flag=false는 자원을 제거하거나 IAM을 회수하지 않는다.

이 파일/파이프라인 구현 승인만으로 main push, workflow 실행, Terraform apply, IAM/DNS/Kubernetes 변경 또는 이미지/chart 게시를 수행하지 않는다. 기존 AWS workflow에서 `scripts/common.sh` 등 공유 helper 변경은 기존 AWS apply 조건을 유지하므로 main push 승인 시 함께 확인한다.
