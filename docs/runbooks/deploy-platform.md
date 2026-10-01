# deploy-platform

현재 iris-platform은 빈 scaffold입니다. 아래 입력과 manifest를 먼저 구현합니다.

1. API·Worker·Agent 이미지 주소와 digest, chart version을 고정합니다.
2. DB/PVC와 EBS CSI, StorageClass를 검증합니다. 초기 DB는 플랫폼 메타데이터용 PostgreSQL입니다.
3. Secret을 별도 생성하고 chart에는 참조 이름만 전달합니다.
4. management의 platform values, Pod Identity SA·역할 연결과 RBAC를 설정합니다.
5. 비밀값 없는 타겟 출력 export와 ConfigMap/등록 인터페이스를 구현하고 적용합니다.
6. 명시한 chart version으로 배포하고 Pod·DB·대상 EKS 접근을 확인합니다.

TODO: 실제 release 이름, Namespace, Helm 명령, DB backup·외부 DB 전환 절차를 확정합니다.
