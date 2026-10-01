# 팀 명령

저장소 루트의 Makefile을 통해 실행합니다. 모든 shell script는 Bash를 사용합니다.

- `terraform-check.sh`: AWS 인증 없이 fmt와 별도로 각 root/module을 init -backend=false / validate합니다. provider 다운로드는 필요합니다.
- `tf-stack.sh`: 허용된 STACK만 실제 디렉토리에 매핑합니다. plan/apply는 `.scaffold` 표시가 있으면 종료합니다. 구현 후 AWS_ACCOUNT_ID를 지정하면 STS 계정 확인과 provider의 allowed_account_ids를 함께 적용합니다.
- `tf-ci.sh`: GitHub main 배포용 bootstrap init/plan/apply. AWS_ACCOUNT_ID·AWS_REGION·TF_STATE_BUCKET을 받고 S3 lock과 저장한 plan을 사용합니다. 로컬 확인은 `plan`, main 자동 배포는 `apply`입니다.
- `helm-check.sh`: 빈 차트의 lint와 AWS·로컬 values render를 확인합니다. 구현 후 manifest 검증을 확장합니다.
- `check-scaffold.py`: Python 표준 라이브러리만으로 파일 구성, JSON, shell 문법을 확인합니다.
- `bootstrap-cluster.sh`, `smoke-test.sh`, `export-targets.sh`: 현재 미구현이며 종료 코드 1을 반환합니다.

구현 후 bootstrap/smoke-test는 선택한 cluster.yaml의 kube-context, AWS 계정과 대상 클러스터를 확인해야 합니다.
외부 addon 이름·버전은 bootstrap에 고정합니다. destroy는 [철거 runbook](../docs/runbooks/teardown.md)에 따릅니다.
