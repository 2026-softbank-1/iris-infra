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

### SSM bridge 생성의 volume RunInstances 거부

`aws_instance.ssm_bridge` 생성 시 `ec2:RunInstances`가 `volume/*`에서 거부되면 CI 역할의 `bridge-launch` 정책 연결과 생성 요청의 소유 태그를 확인합니다. 정책은 리전과 `Project`, `Environment`, `ManagedBy`, `Component` 태그를 제한합니다. AWS provider 6.67.0에서 `root_block_device.tags`는 생성 후에 적용하므로 생성 시 IAM 조건을 충족하지 못합니다. `volume_tags = local.access_tags`로 생성 요청에 `Component=access`까지 전달해야 하며 두 태그 설정은 함께 사용하지 않습니다.

현재 정책이 연결돼 있다면 이 수정에 account 재apply는 필요 없습니다. 부분 적용된 foundation state와 기존 빌드/ECR 자원을 유지하고, 수정 코드가 반영된 main workflow에서 새 plan을 검토하여 삭제·교체 없이 재시도합니다. 수정 전 SHA의 실패한 workflow를 재실행하면 같은 오류가 납니다. foundation 전체 destroy나 IAM 태그 조건 완화로 복구하지 않습니다. IAM simulation과 mock 검사는 실제 EC2 생성 성공을 보장하지 않으며 수정 후 배포 결과에서 별도로 확인합니다.
