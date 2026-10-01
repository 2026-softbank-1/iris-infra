# Terraform

각 bootstrap/account/foundation/management/workload 디렉토리가 독립 실행 단위입니다.
`modules/eks`는 재사용 모듈이며 직접 apply하지 않습니다.

허용 STACK: `bootstrap/aws`, `account/aws`, `aws/dev/foundation`, `aws/dev/management`, `aws/dev/workload`.
모든 root는 아직 리소스가 없는 scaffold입니다. 구현 후 `.scaffold`를 제거합니다.
AWS provider와 버전 조건만 공통 설정했고 출력·remote state 연결은 구현 단계에서 추가합니다.

S3 backend는 `use_lockfile = true`, 암호화와 계정 제한을 사용합니다.
bootstrap의 최초 로컬 state를 원격으로 옮기는 절차는 [runbook](../docs/runbooks/bootstrap.md)에 있습니다.
state 전체를 읽을 수 있는 remote state 권한은 인프라 담당자·CI로 한정합니다.
