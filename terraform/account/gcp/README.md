# GCP account (operator only)

기존 프로젝트와 bootstrap이 만든 GCS bucket을 사용한다. 프로젝트 생성·billing 연결은 관리자가 먼저 완료한다. `gcp/account` state는 자동 CI가 적용하지 않는다. 상세 순서는 [GCP pipeline](../../../docs/runbooks/gcp-pipeline.md)을 따른다.

Service Usage/Resource Manager API를 활성화할 수 있는 관리자 인증으로 account를 적용한다. GitHub 숫자 repository/owner ID, main ref, 허용 event와 정확한 workflow_ref를 검사하는 WIF로 전용 GSA를 사용한다. Argo의 EKS WIF pool과 별개다. 장기 JSON key를 만들지 않는다.

CI에는 프로젝트 범위 `roles/editor`와 workload 자원 생성에 필요한 project IAM/SA/WIF 관리 권한이 있다. Editor는 대부분의 서비스 자원을 관리할 수 있으며 AWS AdministratorAccess와 동일한 권한이나 조직·결제 전체 권한을 보장하지 않는다. 특히 `projectIamAdmin`은 자기 권한을 추가할 수 있고 SA/WIF 관리자 권한은 CI 자체 신뢰 설정에도 영향을 줄 수 있다. **별도 account state와 object prefix는 정상 실행 범위이며, 전체 유효 권한을 격리하지 않는다.** protected main과 workflow/숫자 ID trust를 활성화 전에 실제 OIDC claim과 함께 검증해야 한다. bucket reader와 workload-prefix object grant는 개별 직접 grant의 범위이며 Editor·IAM 관리 권한 전체를 제한하지 않는다. 별도 Secret Manager custom role 자체에는 container 관리만 있고 version access는 포함하지 않는다.

Editor 추가는 관리자가 이 account stack을 apply해야 실제 반영된다. 자동 만료나 임시 옵션은 없다. 회수할 때는 `main.tf`의 `workload_roles`에서 `roles/editor`를 제거하고 관리자 account plan/apply를 수행한다. 기존 IAM/SA/WIF 관리 역할은 유지된다.

API 소유권은 bootstrap=Storage, account=Service Usage/Resource Manager/IAM/IAMCredentials/STS, workload=서비스 API로 분리한다. 기존 state에 API가 이미 있으면 그대로 apply하지 말고 state 이전 계획을 별도 리뷰한다. 자동 workflow는 이 stack과 bootstrap을 적용하지 않는다.
