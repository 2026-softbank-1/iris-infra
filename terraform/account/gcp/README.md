# GCP account (operator only)

기존 프로젝트와 bootstrap이 만든 GCS bucket을 사용한다. 프로젝트 생성·billing 연결은 관리자가 먼저 완료한다. `gcp/account` state는 자동 CI가 적용하지 않는다. 상세 순서는 [GCP pipeline](../../../docs/runbooks/gcp-pipeline.md)을 따른다.

Service Usage/Resource Manager API를 활성화할 수 있는 관리자 인증으로 account를 적용한다. GitHub 숫자 repository/owner ID, main ref, 허용 event와 정확한 workflow_ref를 검사하는 WIF로 전용 GSA를 사용한다. Argo의 EKS WIF pool과 별개다. 장기 JSON key를 만들지 않는다.

CI에는 workload 자원 생성에 필요한 project IAM/SA/WIF 관리 권한이 있다. 특히 `projectIamAdmin`은 자기 권한을 추가할 수 있고 SA/WIF 관리자 권한은 CI 자체 신뢰 설정에도 영향을 줄 수 있다. **별도 account state와 object prefix는 정상 실행 범위 및 직접 접근 제한이며, 침해된 CI에 대한 보안 격리가 아니다.** protected main과 workflow/숫자 ID trust를 활성화 전에 실제 OIDC claim과 함께 검증해야 한다. bucket reader는 다른 prefix 이름을 나열할 수 있지만 object 내용의 직접 접근은 workload prefix만 허용한다. Secret Manager role은 container 관리만 허용하고 version access는 포함하지 않는다.

API 소유권은 bootstrap=Storage, account=Service Usage/Resource Manager/IAM/IAMCredentials/STS, workload=서비스 API로 분리한다. 기존 state에 API가 이미 있으면 그대로 apply하지 말고 state 이전 계획을 별도 리뷰한다. 자동 workflow는 이 stack과 bootstrap을 적용하지 않는다.
