# 팀 명령

GCP 자동 파이프라인 준비·활성화는 [GCP pipeline 런북](../docs/runbooks/gcp-pipeline.md),
운영과 초기 bootstrap은 [GCP 런북](../docs/runbooks/gcp-workload.md)을 따릅니다.
기본 flag=false에서는 계정 없이 검증만 합니다. `make gcp-readiness`는 offline,
`gcp-online-readiness`/`gcp-github-discover`는 명시적 읽기 전용 조회입니다.
`gcp-ci-config`는 account의 nonsecret output만 `.generated/gcp-ci.json`으로 추출합니다.
`tf-* STACK=account/gcp`는 관리자 수동 IAM 설정이며 CI 적용 대상이 아닙니다.
`make gcp-ci-test`는 인증/최신 SHA/동일 saved plan/삭제 차단/취소 cleanup/immutable
이미지 재사용을 fake CLI로 검사합니다. 실제 장시간 ADC refresh와 배포는 별도 확인합니다.
`make gcp-export/gcp-preflight/gcp-smoke`는 cloud/Kubernetes 읽기 전용,
`gcp-access`는 전용 로컬 kubeconfig 생성, `gcp-bootstrap/gcp-backup-keys`는 명시적
원격 변경입니다. 모두 `GCP_PROJECT_ID`를 검증합니다. 최초 GCS state는
`gcp-state-local-init/local-plan/local-apply/migrate`로 별도 local bootstrap에서
migration합니다. 이후 `tf-* STACK=gcp/dev/workload`와 수동 관리자
`STACK=aws/dev/gcp-access`를 사용합니다. `GITOPS_GCP_ENABLED=1`은 Argo 등록 opt-in입니다.
`python3 scripts/tests/test-gcp.py`는 fake identity/회전/cache/예약 Secret/context/CI
범위를 검사하고 `scripts/check-gcp.py`는 Helm 렌더를 검사합니다. 실제 배포 성공은
런북의 HTTPS/uncached pull/Argo resync/격리/key 복구 실험으로 별도 확인합니다.

`make help`와 [private EKS runbook](../docs/runbooks/eks-access.md)을 참고합니다. shell은 Bash를 사용합니다.

| 명령/스크립트 | 동작 |
| --- | --- |
| `make scaffold-check` | 필수 파일·JSON schema·shell syntax·ECR inventory |
| `make tf-check` | fmt + 임시 TF_DATA_DIR의 backend 없는 init/validate |
| `make tf-test` | 실제 state/tfvars/override 제외 임시 복사본의 account/foundation/module/두 EKS mock test |
| `make helm-check` | 고정 Helm/공식 chart 다운로드·lint/render·revision schema·digest·스토리지/replica·온프레미스 gateway gate/공개·비공개/정책 경계 검사(PyYAML6.0.3 필요) |
| `make tf-init/tf-plan/tf-apply STACK=...` | 선택한 root의 실제 backend/AWS 작업. apply는 별도 배포 시 실행 |
| `tf-ci.sh apply` | main CI bootstrap→foundation→management→workload 순차 saved plan apply. account 제외 |
| `check-foundation-plan.py` | foundation plan JSON을 stdin으로 검사. build/ECR/VPC/subnet/NAT0/EIP0 삭제·교체 차단. raw JSON 기록 없음 |
| `make export-targets` | AWS 계정 검증 + Terraform target 출력만 `.generated/targets.json`에 export |
| `check-eks-ci-permissions.py --role-arn ... [--account-plan-json ...]` | 로컬 관리자 인증의 읽기 전용 IAM 시뮬레이션 + Access Analyzer; 배포 실행 없음 |
| `make eks-preflight TARGET=...` | 실제 AWS caller/endpoint/CA/VPC/subnet/SG/SSM 상태 읽기 확인 |
| `make eks-api-tunnel TARGET=...` | preflight 후 SSM remote forwarding과 CA/tls-server-name kubeconfig 생성 |
| `make bootstrap CLUSTER=aws-dev-management` | 두 API/RBAC/nodes/CNI 검증 후 Argo와 root/credentials 설치·addon sync 대기 |
| `make smoke-test TARGET=...` | 읽기 전용 runtime 확인. `--exercise`, `--image-ref`, `--drain-node`는 Bash wrapper로 명시 |

local-workload는 scaffold이며 bootstrap/smoke에서 성공으로 처리하지 않습니다. `.generated`는 0600 로컬 파일이며 state·plan·token·key는 export하지 않습니다. Python ops 실패는 credential/Secret 응답을 출력하지 않습니다.

`python3 scripts/tests/test-tf-ci.py`는 fake AWS/Terraform으로 순서/state key/실패 처리/guard를 검사하고, `test-eks-ops.py`는 wrong account/caller/endpoint·TLS보존·포트충돌·cleanup을 검사합니다. `test-platform-ecr.py`, `test-build-push-ecr.py`는 기존 ECR inventory/빌드 경계를 검사합니다. `IRIS_ECR_DOCKER_TEST=1`은 로컬 scratch 이미지 실제 빌드입니다. 실제 API 허용·통신·Pod Identity·image pull 성공은 mock으로 보장하지 않습니다.

`terraform-ci-changes.py`는 Git event diff로 검사와 배포 입력을 구분합니다. `python3 scripts/tests/test-terraform-ci-changes.py`는 임시 Git 이력과 실제 workflow 조건으로 account/tests/Helm/digest 변경의 apply 제외, 삭제·이름 변경·복수 커밋, 수동 force/main 제한과 변경 감지 실패 차단을 검사합니다. Terraform workflow의 수동 실행은 검증만 수행하며 main의 `force_apply=true`가 실제 apply를 선택합니다([CI 조건](../docs/runbooks/terraform-ci.md#변경-조건과-수동-실행)).

도구 자동 설치·main push·workflow dispatch·Paid 전환·시간 기반 철거는 제공하지 않습니다.

## 온프레미스 Nginx 로컬 검증

`make helm-check`는 gateway의 Helm 렌더와 값 거부 조건을 검사합니다. 다음은 별도로 준비한 Nginx **1.30.x** 실행 파일과 고정 Helm, PyYAML을 사용하는 로컬 프로토콜 검사입니다.

```bash
NGINX=/path/to/nginx-1.30.x HELM=/path/to/helm-3.19.1 python3 scripts/tests/test-onprem-gateway.py
```

임시 디렉터리와 loopback HTTP/DNS 서버에서 Host/path/query·forwarded headers·알 수 없는 Host·WebSocket·DNS 재조회·upstream 중단 후 복구를 검사합니다. 운영 서버·시스템 설정·클러스터를 변경하지 않습니다. 공식 1.30.x 소스로 만든 로컬 binary의 성공은 고정 OCI 이미지의 실행, CNI·Tailscale·ALB·TLS 경로의 성공을 보장하지 않습니다.

## EKS CI 권한 검사

아래 절차는 임시 Admin이 비활성화된 scoped 권한 모드에서 사용합니다. 대회 기간 기본값인
`enable_temporary_admin_access=true`에서는 plan 모드가 외부 AWS 관리형 정책을 거부하고,
live 모드는 Admin이 허용하는 작업과 negative 거부 기대가 충돌하므로 통과 기준으로 사용하지 않습니다.
검사 로직과 negative 보호는 유지합니다. Admin 모드에서는 관리자 account plan과 적용 후 attachment 조회로 연결을 확인합니다.
대회 종료 후 로컬 입력에 `false`를 지속 저장하고 관리자 account apply로 회수한 뒤 아래 검사를 다시 사용합니다.
자세한 절차는 [임시 Admin 적용·회수](../terraform/account/aws/README.md#대회-기간-임시-admin)를 참고합니다.

적용된 권한은 로컬 관리자 AWS profile로 다음 명령을 실행합니다. role ARN은 account 출력 `terraform_apply_role_arn`입니다. CI 역할을 assume하거나 trust를 변경하지 않습니다.

```bash
python3 scripts/check-eks-ci-permissions.py \
  --role-arn arn:aws:iam::187069338876:role/iris-dev-github-terraform
```

다른 구성은 `--region`, `--project`, `--environment`, `--management-cluster-name`, `--workload-cluster-name`을 함께 전달합니다. 기본값은 서울/iris/dev/iris-dev-management/iris-dev-workload입니다. caller와 role의 계정이 같아야 합니다. 관리자에게 IAM 역할·정책 read 및 SimulatePrincipalPolicy/SimulateCustomPolicy, STS GetCallerIdentity, Access Analyzer ValidatePolicy 권한이 필요합니다. GitHub deploy job에 이 권한을 추가하지 않습니다.

account 적용 전 예정 정책을 검사하려면 실제 account 입력/backend를 준비한 후 다음을 실행합니다. 임시 plan·JSON에는 민감한 값이 포함될 수 있으므로 Git이나 artifact에 저장하지 않습니다.

```bash
umask 077
audit_tmp="$(mktemp -d)"
terraform -chdir=terraform/account/aws plan -out="$audit_tmp/account.tfplan"
terraform -chdir=terraform/account/aws show -json "$audit_tmp/account.tfplan" > "$audit_tmp/account.json"
python3 scripts/check-eks-ci-permissions.py \
  --role-arn arn:aws:iam::187069338876:role/iris-dev-github-terraform \
  --account-plan-json "$audit_tmp/account.json"
rm -rf "$audit_tmp"
```

plan 모드는 계정·리전·이름·태그 입력을 plan에서 읽으며 CLI override와 충돌하면 실패합니다. 대상 역할의 attachment와 ARN이 일치하는 managed policy, role 이름이 일치하는 inline policy만 선택하고 필수 inventory를 확인합니다. 관련 값이 unknown인 최초 plan, 외부 정책 문서, 별도 정책 inventory로 확인할 수 없는 embedded role policy 등의 연결은 실패합니다. role의 조회 값에 중복 표시된 정책은 문서·연결이 일치할 때만 인정합니다. 전체 plan이나 AWS 오류 원문은 출력하지 않습니다.

행렬은 AWS provider 6.67.0의 현재 리소스·waiter·tag 호출과 AWS 권한 참조를 근거로 유지합니다. positive는 모두 허용, negative는 모두 거부되어야 하고 페이지 누락·중복·예상 밖 결과·관련 context 누락은 실패입니다. 정책 Action 목록을 읽어 기대 API를 생성하지 않습니다.

결과의 live/custom 출처를 구분합니다. custom 통과는 예정 identity policy의 허용 검사이며 boundary/session/resource policy/SCP를 포함하지 않습니다. live도 실제 GitHub OIDC·서비스 요청·quota·네트워크·배포 성공 검사가 아닙니다. CLI 오류나 정책 검사 실패도 nonzero로 끝납니다. AWS CLI의 자동 pagination과 adaptive retry를 사용하고 요청은 순차 배치합니다.

`python3 scripts/tests/test-eks-ci-permissions.py`는 fake AWS CLI로 정책 선택·unknown/누락·거부·결과 완전성·계정 불일치·오류 출력 보호를 검사합니다. IAM 의미 자체는 실제 AWS simulation으로 별도 확인합니다.
