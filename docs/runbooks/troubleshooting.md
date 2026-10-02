# troubleshooting

AWS 계정, target ID, kube-context, Namespace와 release를 먼저 확인합니다.

- 빌드: 고정 SHA, Job 상태·종료 코드·로그, ECR push와 Pod Identity를 확인합니다.
- 앱: `kubectl --context CONTEXT -n NAMESPACE get pods`, `describe pod POD`, `logs POD`로 이벤트와 로그를 봅니다.
- image pull: digest·repository, 노드 pull 권한과 네트워크 경로를 확인합니다.
- 접근: IAM EKS 인증, Access Entry, `kubectl auth can-i`와 Namespace RBAC를 함께 확인합니다.
- Ingress: controller 이벤트, class, Service endpoint, health path와 DNS를 확인합니다.
- 스토리지: PVC/PV, StorageClass와 CSI addon 상태를 확인합니다.

TODO: 실제 Namespace·Job·release 이름과 로그 위치를 구현 후 추가합니다.

## Private EKS·Argo·관측 스택

- caller/account/endpoint/CA/SG 변경 오류: 정확한 IAM operator로 로그인하고 target output을 다시 export합니다. public API를 열거나 인증서 검증을 우회해 해결하지 않습니다.
- SSM Offline/포워딩 실패: private bridge·NAT HTTPS/DNS·SSM role/Agent 버전·운영자 StartSession 권한·로컬 Session Manager plugin과 포트 충돌을 확인합니다.
- node NotReady 또는 allocatable.pods≠35: pinned AL2023 nodeadm cloud-final drop-in/MNG user data·prefix delegation·subnet contiguous /28 여유를 확인합니다. bootstrap은 이 조건에서 쓰기 전에 멈춥니다.
- Argo AWS auth 실패: exact 3개 SA·Pod Identity Agent·management role session tag trust/자기 assume·deploy role trust/TagSession·Access Entry를 확인합니다. AppProject 제한은 cluster-admin IAM 축소가 아닙니다.
- Git sync 실패: read-only private credential의 실제 저장소 접근, 검토 SHA 존재, GitHub SSH known_hosts를 확인합니다. Secret 본문이나 token을 로그로 공유하지 않습니다.
- metrics 수집/TLS 실패: metrics-server의 service-account CA/kubelet serving CSR·node10250 SG, Prometheus healthy kubelet/KSM/node exporter/apiserver job을 확인합니다. unreachable EKS controller/scheduler/etcd/kube-proxy 수집은 꺼져 있습니다.
- PVC Pending: EBS CSI Pod Identity·gp3 encrypted/WFFC·consumer scheduling/AZ·EBS quota를 확인합니다. singleton EBS 서비스는 다른 AZ로 즉시 failover하지 않습니다.
- exercise 실패: 출력된 임시 namespace/Pod만 삭제하고 지정 drain node를 uncordon합니다. 보호 build/ECR/state는 정리 대상이 아닙니다.
- CI IAM 또는 STS 실패: account 권한과 maxsession7200을 main merge 전에 적용했는지 확인합니다. 실패한 후행 stack은 앞선 성공 stack을 되돌리지 않습니다.
