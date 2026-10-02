# 팀 명령

`make help`와 [private EKS runbook](../docs/runbooks/eks-access.md)을 참고합니다. shell은 Bash를 사용합니다.

| 명령/스크립트 | 동작 |
| --- | --- |
| `make scaffold-check` | 필수 파일·JSON schema·shell syntax·ECR inventory |
| `make tf-check` | fmt + 임시 TF_DATA_DIR의 backend 없는 init/validate |
| `make tf-test` | 실제 state/tfvars/override 제외 임시 복사본의 account/foundation/module/두 EKS mock test |
| `make helm-check` | 고정 Helm/공식 chart 다운로드·lint/render·Git SHA schema·digest·스토리지/replica 검사(PyYAML6.0.3 필요) |
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

도구 자동 설치·main push·workflow dispatch·Paid 전환·시간 기반 철거는 제공하지 않습니다.

## EKS CI 권한 검사

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
