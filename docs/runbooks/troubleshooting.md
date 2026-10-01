# troubleshooting

AWS 계정, target ID, kube-context, Namespace와 release를 먼저 확인합니다.

- 빌드: 고정 SHA, Job 상태·종료 코드·로그, ECR push와 Pod Identity를 확인합니다.
- 앱: `kubectl --context CONTEXT -n NAMESPACE get pods`, `describe pod POD`, `logs POD`로 이벤트와 로그를 봅니다.
- image pull: digest·repository, 노드 pull 권한과 네트워크 경로를 확인합니다.
- 접근: IAM EKS 인증, Access Entry, `kubectl auth can-i`와 Namespace RBAC를 함께 확인합니다.
- Ingress: controller 이벤트, class, Service endpoint, health path와 DNS를 확인합니다.
- 스토리지: PVC/PV, StorageClass와 CSI addon 상태를 확인합니다.

TODO: 실제 Namespace·Job·release 이름과 로그 위치를 구현 후 추가합니다.
