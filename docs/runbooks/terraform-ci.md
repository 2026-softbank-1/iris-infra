# Terraform CI와 main 자동 배포

`.github/workflows/terraform-check.yml`이 전체 흐름을 관리합니다.

- PR: scaffold, CI 실행 순서 테스트, fmt, backend 없는 init/validate, mock IAM 테스트를 수행합니다. AWS 자원은 변경하지 않습니다.
- main push(머지 포함): 검증 성공 후 GitHub OIDC로 AWS 인증하고 구현된 stack을 순서대로 init → plan → apply합니다. 각 stack은 저장한 plan을 그대로 적용합니다.
- Actions의 Run workflow: main을 선택하면 같은 검증·배포를 실행합니다. 다른 브랜치에서는 검증만 실행합니다.

자동 apply 순서와 현재 구현 상태는 다음과 같습니다.

| 순서 | STACK | S3 state key | 현재 동작 |
| --- | --- | --- | --- |
| 1 | `bootstrap/aws` | `bootstrap/aws/terraform.tfstate` | state 버킷 설정 적용 |
| 2 | `aws/dev/foundation` | `aws/dev/foundation/terraform.tfstate` | S3·CodeBuild·로그·빌드 역할 적용 |
| 3 | `aws/dev/management` | `aws/dev/management/terraform.tfstate` | `.scaffold`가 있어 건너뜀 |
| 4 | `aws/dev/workload` | `aws/dev/workload/terraform.tfstate` | `.scaffold`가 있어 건너뜀 |

`account/aws`는 GitHub 인증 역할의 초기 설정과 변경을 관리자가 적용하는 stack입니다.
`terraform/modules/eks`는 재사용 module이므로 독립 apply하지 않습니다.
CI는 `scripts/tf-ci.sh`에 나열된 root만 실행하며 디렉터리를 재귀적으로 찾아 배포하지 않습니다.
모든 실행 대상의 lock 파일과 S3 backend 선언을 먼저 확인합니다. bootstrap의 `.scaffold`는 오류입니다.
후속 stack은 리소스·입력·state 의존성과 CI IAM 권한을 준비한 후 `.scaffold`를 제거합니다.
새 root를 추가할 때는 실행 순서·state key·state 권한도 함께 확장합니다.

## 최초 AWS 인증 준비

로컬의 `aws login` 세션은 GitHub runner에서 사용할 수 없습니다.
GitHub OIDC가 실행마다 임시 자격 증명을 발급받으며 장기 AWS access key는 등록하지 않습니다.

1. bootstrap 버킷을 생성하고 state를 S3로 이전합니다.
2. 아래 API로 저장소의 실제 OIDC subject prefix를 확인합니다.

```bash
gh api repos/2026-softbank-1/iris-infra/actions/oidc/customization/sub
```

현재 저장소는 immutable subject를 사용합니다.
`terraform/account/aws/terraform.tfvars.example`의 prefix에는 확인한 저장소·조직 ID가 포함되어 있습니다.
커스텀 subject를 사용하는 경우 실제 claim에 맞춰 trust policy도 변경해야 합니다.
trust는 정확한 prefix의 `:ref:refs/heads/main`과 `sts.amazonaws.com` audience만 허용합니다.

3. account의 `.example` 파일을 로컬 `terraform.tfvars`와 `backend.hcl`로 복사하고 실제 계정·버킷·prefix를 입력합니다.
4. 관리자 인증으로 최초 역할을 생성합니다.

```bash
export AWS_PROFILE=iris-tf
export AWS_ACCOUNT_ID=187069338876
make tf-init STACK=account/aws
make tf-plan STACK=account/aws
make tf-apply STACK=account/aws
terraform -chdir=terraform/account/aws output -raw terraform_apply_role_arn
```

기존 GitHub OIDC provider가 있다면 해당 ARN을 `aws_iam_openid_connect_provider.github`로 import합니다.
초기 생성에는 OIDC provider, 역할, inline policy 2개로 총 4개가 포함됩니다.
`bootstrap-state-bucket` 정책은 state 버킷 설정과 위 4개 state의 읽기·쓰기, lock 읽기·쓰기·삭제를 허용합니다.
state 본문·이전 버전·state 버킷 삭제와 account state 접근은 허용하지 않습니다.
`foundation-build-resources` 정책은 현재 foundation의 빌드 입력 버킷, CodeBuild 프로젝트,
로그 그룹, CodeBuild·Build Worker 역할 2개의 관리를 허용합니다.
`iam:PassRole`은 해당 CodeBuild 역할을 CodeBuild에 전달할 때만 허용합니다.
CI 자신의 인증 역할·OIDC provider 관리, ECR 저장소 생성과 사용자 빌드 시작 권한은 포함하지 않습니다.
로그 그룹 탐색은 리소스 제한을 지원하지 않아 지정 리전의 `logs:DescribeLogGroups`만 `*`를 사용합니다.

## 기존 bootstrap 전용 CI 확장

코드에 정책을 추가해도 AWS의 기존 역할 권한은 즉시 바뀌지 않습니다.
배포 코드가 main에 반영되기 전에 관리자가 변경된 account 설정으로 plan을 검토하고 적용해야 합니다.
기존 역할·state를 그대로 사용하고, 새 foundation 정책과 state 접근 범위 확장을 확인합니다.

```bash
export AWS_PROFILE=iris-tf
export AWS_ACCOUNT_ID=187069338876
make tf-init STACK=account/aws
make tf-plan STACK=account/aws
# 위 계획을 검토한 뒤 실행
make tf-apply STACK=account/aws
```

foundation을 이미 다른 위치에서 적용했다면 실제 자원과 state 위치를 먼저 확인합니다.
로컬 state는 기존 내용을 보존하고 foundation의 `backend.hcl`을 위 S3 key로 설정한 뒤 이전합니다.

```bash
terraform -chdir=terraform/environments/aws/dev/foundation init -migrate-state -backend-config=backend.hcl
```

다른 remote key의 state도 내용과 자원 주소를 확인한 후 이전합니다.
Terraform 외부에서 생성된 자원을 이 stack이 관리해야 한다면 해당 자원을 import합니다.
기존 자원을 빈 state로 다시 생성하지 않습니다. 현재 foundation은 처음 생성할 경우 자원 11개입니다.

권한 적용과 state 확인을 마친 뒤 main 머지 또는 main workflow 실행으로 실제 배포를 확인합니다.
IAM mock 테스트는 정책 구조를 검증하며 실제 AWS 권한의 충분성을 보장하지 않습니다.
VPC·EKS 구현 시 해당 자원의 권한도 관리자 account 적용으로 먼저 추가해야 합니다.

## GitHub repository variables

Settings → Secrets and variables → Actions → Variables에서 다음 값을 설정합니다.

| 이름 | 현재 환경 값 |
| --- | --- |
| `AWS_ACCOUNT_ID` | `187069338876` |
| `AWS_REGION` | `ap-northeast-2` |
| `TF_STATE_BUCKET` | `iris-tfstate-187069338876-ap-northeast-2` |
| `TERRAFORM_APPLY_ROLE_ARN` | account의 `terraform_apply_role_arn` 출력 |

위 값들은 자격 증명 비밀값이 아닙니다. AWS key/session token이나 로컬 AWS_PROFILE은 등록하지 않습니다.

## 배포와 확인

각 stack과 EKS module의 `.terraform.lock.hcl`을 코드와 함께 commit합니다.
CI는 commit된 lock 파일을 변경하지 않고 provider를 설치합니다.
현재 CI 입력은 계정·리전을 variables에서 받으며 각 stack의 project=iris, environment=dev 기본값을 사용합니다.
로컬 `.tfvars`의 다른 입력을 사용하는 경우 CI 입력도 함께 맞춰야 합니다.
`terraform.tfvars`, `backend.hcl`, `.terraform`, state·plan 파일은 commit하지 않습니다.

main에 코드가 반영되면 check → deploy 순서로 실행됩니다.
deploy는 전체 순서를 하나의 job에서 수행하며 같은 concurrency group으로 동시 apply를 막습니다.
기존 실행과도 직렬화하기 위해 group 이름 `terraform-apply-bootstrap-aws`를 유지합니다.
진행 중인 apply를 새 push로 취소하지 않습니다.
Terraform도 S3 lock과 5분 lock 대기를 사용합니다.
plan 파일은 stack마다 생성해 apply한 뒤 삭제하며 실패 시에도 정리하고 artifact로 공개하지 않습니다.

이미 만들어진 bootstrap은 `No changes`가 예상되며 foundation은 해당 state와 실제 자원의 차이를 적용합니다.
코드 변경에 따른 생성·수정·삭제가 자동 적용됩니다. foundation 입력 버킷은 기존 `force_destroy = true`이므로
삭제·교체 계획에는 입력 객체 삭제도 포함될 수 있습니다. Terraform apply 자체는 CodeBuild 빌드를 시작하지 않습니다.
성공·실패와 scaffold skip은 GitHub Actions 로그에서 확인합니다.

한 stack에서 실패하면 다음 stack은 실행하지 않습니다. 앞서 성공한 apply는 자동으로 되돌리지 않습니다.
CI 코드만 되돌려도 이미 생성된 AWS 자원이 삭제되지는 않습니다. 복구는 실제 state와 plan을 검토하여 진행합니다.
`bash scripts/tf-ci.sh plan`은 적용 없이 각 stack을 계획하지만, 선행 stack의 새 output을 아직 적용하지 않았다면
후행 stack이 그 output을 읽을 수 없어 최초 전체 구성을 계획하는 데 제한이 있습니다.

OIDC 인증 실패 시 repository variable의 역할 ARN과 실제 subject prefix·main 브랜치 조건을 확인합니다.
S3 AccessDenied는 state key와 `.tflock` 권한을 확인합니다.

## Provider lock 파일 갱신

로컬 Mac은 `darwin_arm64`, GitHub Actions runner는 `linux_amd64`를 사용합니다.
CI는 `-lockfile=readonly`로 초기화하므로 두 플랫폼의 provider 체크섬을 미리 등록해야 합니다.
Mac에서만 초기화한 lock 파일은 Linux에서 provider 설치 후 체크섬 검증 오류를 일으킬 수 있습니다.

저장소 루트에서 다음 명령으로 6개 lock 파일을 갱신합니다.
`providers lock`은 공식 registry에서 각 플랫폼 패키지를 내려받아 검증하며 AWS 자원을 변경하지 않습니다.
provider 버전은 기존 lock의 선택을 유지합니다.

```bash
for dir in \
  terraform/bootstrap/aws \
  terraform/account/aws \
  terraform/environments/aws/dev/foundation \
  terraform/environments/aws/dev/management \
  terraform/environments/aws/dev/workload \
  terraform/modules/eks; do
  terraform -chdir="$dir" providers lock \
    -platform=darwin_arm64 -platform=linux_amd64
done
```

공식 패키지 서명과 lock 파일 diff를 확인한 뒤 `make tf-check`를 실행합니다.
provider를 의도적으로 업그레이드할 때도 두 플랫폼의 체크섬을 갱신하고,
변경된 lock 파일 전체를 코드와 함께 commit합니다.

공식 문서: [GitHub OIDC in AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws),
[Terraform S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3),
[AWS PassRole](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html),
[Terraform providers lock](https://developer.hashicorp.com/terraform/cli/commands/providers/lock).
