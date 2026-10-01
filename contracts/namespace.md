# namespace

상태: 합의 전 초안.

프로젝트 Namespace 생성은 프로젝트 등록 단계의 클러스터 범위 권한으로 수행합니다.
일반 앱 배포는 대상 Namespace 범위로 제한합니다. 초기 샘플 Namespace는 bootstrap에서 만들 수 있습니다.

Quota·LimitRange·RBAC와 실제 적용이 검증된 NetworkPolicy를 사용합니다.
IAM EKS 인증과 Kubernetes RBAC를 함께 검증합니다.

TODO: Namespace 이름 규칙, 생성 주체, 역할 연결, 삭제 수명 주기와 정책 적용 방식을 확정합니다.
