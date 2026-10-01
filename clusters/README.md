# 클러스터 설정

관리 AWS EKS, 앱 AWS EKS, 로컬 k3d의 Git 타겟 정의와 values를 관리합니다.
`cluster.yaml`은 Kubernetes manifest가 아닙니다. kubeconfig는 각 실행 환경에서 생성합니다.
비밀값 없는 출력은 향후 `scripts/export-targets.sh`에서 `.generated/targets.json`으로 합성합니다.
인증 참조와 플랫폼 등록 인터페이스는 [타겟 계약](../contracts/target.md)에서 합의합니다.
