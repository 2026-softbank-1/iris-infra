# deploy-platform

현재 iris-platform은 빈 scaffold입니다. 아래 입력과 manifest를 먼저 구현합니다.

1. API·Worker·Agent 이미지 주소와 digest, chart version을 고정합니다.
2. DB/PVC와 EBS CSI, StorageClass를 검증합니다. 초기 DB는 플랫폼 메타데이터용 PostgreSQL입니다.
3. Secret을 별도 생성하고 chart에는 참조 이름만 전달합니다.
4. management의 platform values, Pod Identity SA·역할 연결과 RBAC를 설정합니다.
5. 비밀값 없는 타겟 출력 export와 ConfigMap/등록 인터페이스를 구현하고 적용합니다.
6. 명시한 chart version으로 배포하고 Pod·DB·대상 EKS 접근을 확인합니다.

TODO: 실제 release 이름, Namespace, Helm 명령, DB backup·외부 DB 전환 절차를 확정합니다.

## EKS와 공통 addon 선행 상태

관리/앱 EKS와 Argo CD·baseline·LBC·metrics·관측 스택은 [private EKS runbook](eks-access.md)의 별도 bootstrap으로 설치합니다. Terraform은 build-worker SA의 Pod Identity association까지 준비하며 실제 Worker·사용자 앱 ApplicationSet/GitOps 저장소 연동·API/DB는 여전히 후속 범위입니다. 사용자 앱은 ADR 0002에 따라 Worker의 values commit을 Argo가 동기화합니다. 이번 bootstrap은 addon Applications만 설치합니다. 빈 플랫폼 ECR은 addon 설치를 막지 않으며 존재하는 digest만 opt-in pull 검사합니다. 기본 외부 ALB/도메인/ACM 설정은 없습니다.
