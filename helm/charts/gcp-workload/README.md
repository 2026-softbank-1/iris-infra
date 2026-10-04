# GCP workload addon

GKE Gateway API가 활성화된 클러스터의 `iris-system`에 설치합니다.
Terraform의 NamedAddress·CertificateMap을 사용해 shared global HTTPS Gateway와
HTTP→HTTPS redirect를 만듭니다. HTTPS route는 `iris.dev/target=gcp-dev-workload`
namespace label로 연결을 제한합니다.

ECR 갱신기는 기본 off입니다. 게시된 ECR digest, Google SA email/unique ID,
AWS account/read-only role, audience와 **실제 Kubernetes API endpoint IP /32**를
입력할 때만 활성화합니다. Dataplane V2 ipBlock에는 Service clusterIP를 넣지 않습니다.
`make gcp-bootstrap`이 EndpointSlice에서 이 값을 구하고 초기 credential을 검증합니다.

helper 자신을 pull하는 `iris-system/iris-ecr-pull` Secret은 bootstrap 운영자가
소유하므로 chart는 빈 Secret을 렌더하지 않습니다. helper는 이를 스스로 갱신합니다.
namespace get/list만 ClusterRole에, 예약 Secret get/patch만 각 namespace Role에
부여합니다. Secret create/list나 사용자 변수 Secret 권한은 없습니다.

장애로 12시간 토큰이 만료되면 실행 중 서비스는 계속될 수 있지만 신규 Pod pull은
실패합니다. helper도 새 노드에서 다시 pull하려면 운영자가 bootstrap token을 재주입해야
합니다. 회전/실제 pull/Argo resync 검증은 [GCP 런북](../../../docs/runbooks/gcp-workload.md).
