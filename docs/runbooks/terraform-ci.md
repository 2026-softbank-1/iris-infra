# Terraform CI와 main 자동 배포

`.github/workflows/terraform-check.yml`이 전체 흐름을 관리합니다.

- PR: scaffold, fmt, backend 없는 init/validate, mock IAM 테스트를 수행합니다. AWS 자원은 변경하지 않습니다.
- main push(머지 포함): 검증 성공 후 GitHub OIDC로 AWS 인증하고 bootstrap의 plan을 저장한 뒤 그대로 apply합니다.
- Actions의 Run workflow: main을 선택하면 같은 검증·배포를 실행합니다. 다른 브랜치에서는 검증만 실행합니다.

현재 자동 apply 대상은 구현된 `terraform/bootstrap/aws`입니다.
`account/aws`는 GitHub 인증 역할의 초기 설정과 변경을 관리자가 적용하는 stack입니다.
foundation/management/workload는 아직 scaffold이며 자동 apply하지 않습니다.
후속 stack 구현 시 `scripts/tf-ci.sh`의 대상·state key와 IAM 역할 권한을 함께 확장합니다.

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
초기 생성에는 OIDC provider, 역할, inline policy 3개가 포함됩니다.
권한은 해당 state 버킷 설정 변경과 bootstrap state 읽기·쓰기, lock 읽기·쓰기·삭제로 한정됩니다.
state 자체 삭제와 bucket 삭제, IAM 변경 권한은 CI에 부여하지 않습니다.

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

bootstrap과 account의 `.terraform.lock.hcl`을 코드와 함께 commit합니다.
CI는 commit된 lock 파일을 변경하지 않고 provider를 설치합니다.
현재 bootstrap 입력은 계정·리전을 variables에서 받으며 project=iris, environment=dev 기본값을 사용합니다.
로컬 `.tfvars`의 다른 입력을 사용하는 경우 CI 입력도 함께 맞춰야 합니다.
`terraform.tfvars`, `backend.hcl`, `.terraform`, state·plan 파일은 commit하지 않습니다.

main에 코드가 반영되면 check → deploy 순서로 실행됩니다.
deploy는 같은 stack에 대해 동시 실행하지 않으며 진행 중인 apply를 새 push로 취소하지 않습니다.
Terraform도 S3 lock과 5분 lock 대기를 사용합니다.
plan 파일은 같은 job에서 apply한 뒤 삭제하며 artifact로 공개하지 않습니다.

최초 정상 실행에서는 이미 만들어진 버킷이므로 `No changes`와 `Resources: 0 added, 0 changed, 0 destroyed`가 예상됩니다.
코드 변경이 있다면 그 차이가 자동 적용됩니다. 성공·실패는 GitHub Actions 로그에서 확인합니다.

OIDC 인증 실패 시 repository variable의 역할 ARN과 실제 subject prefix·main 브랜치 조건을 확인합니다.
S3 AccessDenied는 state key와 `.tflock` 권한을 확인합니다.

공식 문서: [GitHub OIDC in AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws),
[Terraform S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3).
