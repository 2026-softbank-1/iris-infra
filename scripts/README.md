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
| `make eks-preflight TARGET=...` | 실제 AWS caller/endpoint/CA/VPC/subnet/SG/SSM 상태 읽기 확인 |
| `make eks-api-tunnel TARGET=...` | preflight 후 SSM remote forwarding과 CA/tls-server-name kubeconfig 생성 |
| `make bootstrap CLUSTER=aws-dev-management` | 두 API/RBAC/nodes/CNI 검증 후 Argo와 root/credentials 설치·addon sync 대기 |
| `make smoke-test TARGET=...` | 읽기 전용 runtime 확인. `--exercise`, `--image-ref`, `--drain-node`는 Bash wrapper로 명시 |

local-workload는 scaffold이며 bootstrap/smoke에서 성공으로 처리하지 않습니다. `.generated`는 0600 로컬 파일이며 state·plan·token·key는 export하지 않습니다. Python ops 실패는 credential/Secret 응답을 출력하지 않습니다.

`python3 scripts/tests/test-tf-ci.py`는 fake AWS/Terraform으로 순서/state key/실패 처리/guard를 검사하고, `test-eks-ops.py`는 wrong account/caller/endpoint·TLS보존·포트충돌·cleanup을 검사합니다. `test-platform-ecr.py`, `test-build-push-ecr.py`는 기존 ECR inventory/빌드 경계를 검사합니다. `IRIS_ECR_DOCKER_TEST=1`은 로컬 scratch 이미지 실제 빌드입니다. 실제 API 허용·통신·Pod Identity·image pull 성공은 mock으로 보장하지 않습니다.

도구 자동 설치·main push·workflow dispatch·Paid 전환·시간 기반 철거는 제공하지 않습니다.
