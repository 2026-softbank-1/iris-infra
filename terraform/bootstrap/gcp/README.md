# GCP state bootstrap

기존 GCP 프로젝트와 billing, Service Usage·Storage API/IAM 권한이 필요합니다.
프로젝트·billing을 생성하지 않습니다. Google provider 7.x의 고정 lock을 사용합니다.

`terraform.tfvars.example`을 복사하고 `project_id`, 전역 고유 `state_bucket_name`을
입력합니다. `backend.hcl.example`은 생성할 **같은 bucket**, prefix `gcp/bootstrap`으로
복사합니다. 첫 bucket을 만들 때는 `make gcp-state-local-init`, `local-plan`,
`local-apply`를 순서대로 실행합니다. 전용 `.generated/gcp-state-bootstrap` 디렉터리에서
local backend로 생성하므로 아직 없는 bucket을 backend로 초기화하지 않습니다.
`make gcp-state-migrate`는 같은 디렉터리의 backend를 GCS로 전환해 대화형
`init -migrate-state`를 실행합니다. 이후 `make tf-init STACK=bootstrap/gcp`,
`tf-plan`으로 source root에서도 remote state를 확인합니다.

모든 명령에 `GCP_PROJECT_ID`를 명시하며 plan/apply는 같은 project를 provider에
전달합니다. Terraform ADC와 gcloud 운영자 인증 모두 해당 프로젝트 권한이 필요합니다.
local state·backup은 migration 및 remote state 확인이 끝날 때까지 안전하게 보존합니다.
`.generated`와 실제 tfvars/backend는 Git에서 제외됩니다.

bucket은 서울, uniform access/public 차단/versioning, force_destroy=false,
prevent_destroy로 보호합니다. 생성 이후 bucket 이름·location 변경이나 destroy는
별도 검토가 필요합니다. 자세한 순서는 [GCP 런북](../../../docs/runbooks/gcp-workload.md).
