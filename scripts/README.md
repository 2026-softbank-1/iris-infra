# 팀 명령

저장소 루트의 Makefile을 통해 실행합니다. 모든 shell script는 Bash를 사용합니다.

- `terraform-check.sh`: AWS 인증 없이 fmt와 별도로 각 root/module을 init -backend=false / validate합니다. provider 다운로드는 필요합니다.
- foundation의 `terraform test`: mock provider로 기본·사용자 지정 네트워크, 단일/AZ별 NAT, route·SG·태그·출력, 입력 오류와 example/default 일치를 검사합니다. account의 `terraform test`는 기존 trust/state/build 보호와 네트워크 CI 정책 범위를 검사합니다. AWS 자격 증명 없이 실행하며 실제 통신·IAM 충분성 검증은 아닙니다.
- `tf-stack.sh`: 허용된 STACK만 실제 디렉토리에 매핑합니다. plan/apply는 `.scaffold` 표시가 있으면 종료합니다. 구현 후 AWS_ACCOUNT_ID를 지정하면 STS 계정 확인과 provider의 allowed_account_ids를 함께 적용합니다.
- `tf-ci.sh`: GitHub main 배포용 root 순차 init/plan/apply. bootstrap → foundation → management → workload 순서이며 downstream `.scaffold`는 로그를 남기고 건너뜁니다. AWS_ACCOUNT_ID·AWS_REGION·TF_STATE_BUCKET을 받고 stack별 S3 state·lock과 저장한 plan을 사용합니다. 실행 전 전체 lock/backend 확인, 실패 시 중단·plan 삭제를 수행합니다. account와 재사용 module은 배포하지 않습니다. 로컬 확인은 `plan`, main 자동 배포는 `apply`입니다.
- `tests/test-tf-ci.py`: `python3 scripts/tests/test-tf-ci.py`로 실행하는 CI 순서·state key·계정 확인·scaffold·실패 처리 검사입니다. fake AWS/Terraform 실행 파일을 사용하며 실제 API나 자원을 변경하지 않습니다.
- `helm-check.sh`: 빈 차트의 lint와 AWS·로컬 values render를 확인합니다. 구현 후 manifest 검증을 확장합니다.
- `check-scaffold.py`: Python 표준 라이브러리만으로 파일 구성, JSON, shell 문법을 확인합니다.
- `bootstrap-cluster.sh`, `smoke-test.sh`, `export-targets.sh`: 현재 미구현이며 종료 코드 1을 반환합니다.

구현 후 bootstrap/smoke-test는 선택한 cluster.yaml의 kube-context, AWS 계정과 대상 클러스터를 확인해야 합니다.
외부 addon 이름·버전은 bootstrap에 고정합니다. destroy는 [철거 runbook](../docs/runbooks/teardown.md)에 따릅니다.
