# 0001: 저장소와 배포 구조

상태: 초기 설계 방향. 실제 자원 구현은 대기 중입니다.

관리 EKS와 앱 EKS를 분리해 플랫폼·빌드 작업과 사용자 워크로드의 운영 경계를 나눕니다.
초기에는 공통 VPC를 사용하고 연결 경로·보안 그룹을 foundation에서 관리합니다.
bootstrap/account는 개발 클러스터보다 긴 별도 수명 주기를 갖습니다.

배포는 BuildKit Job + Deployer Worker + Helm 직접 호출을 사용합니다.
원본 chart는 이 저장소 하나에서 관리하고 Worker·CLI에 명시적 버전으로 전달합니다.
배포 상태는 DB, 비밀값은 SSM/Secret에 저장합니다.

후속 검증: VPC 비용·접근 경로, 빌드 격리, Pod Identity와 Access Entry·RBAC,
외부 Ingress·image pull, 실패 복구와 로컬 호환성.
