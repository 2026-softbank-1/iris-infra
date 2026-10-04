# 0007: GKE에서 독립 서비스 경로를 갖는 dev workload

상태: 채택(코드 및 로컬 검증, 실제 배포 전)

AWS 외에서도 사용자 서비스를 실행하며 관리 EKS의 Argo CD와 ECR은 공유한다.
첫 대상은 서울 GKE Standard zonal dev cluster의 1–2 노드다. GCP 자체 VPC/Gateway/
fixed IP/wildcard TLS/NAT/state/Sealed Secrets key를 사용해 사용자 HTTP 요청이 AWS
터널·온프레미스 proxy를 거치지 않게 한다.

Terraform은 cloud resource, Argo는 Gateway와 workload를 소유한다. AWS Argo는
EKS projected token → 외부 Google WIF/SA impersonation → GKE DNS API로 접속한다.
IP endpoint는 끄고 private nodes를 사용한다. Google IAM은 cluster connect/get만
해당 cluster로 제한하며 dev Kubernetes cluster-admin을 명시적으로 사용한다.
AppProject는 이 RBAC를 축소하지 않는다.

GKE native registry 인증으로 ECR을 pull할 수 없으므로 제한된 갱신기가 Google SA
ID token → AWS STS → ECR의 단기 인증을 예약 Secret에 갱신한다. namespace label은
선택 규칙이고 이름 고정 RoleBinding이 접근 경계다. helper 자체 최초 pull과 만료
복구는 운영자의 초기 token 주입을 사용한다. 장기 SA/AWS key는 생성하지 않는다.

AWS target v1을 유지하고 별도 GCP infrastructure contract를 추가한다. GCP service
chart는 새 0.10.0 태그에 고정하며 GCP Gateway 경로는 앱 Deployment·ROLLING만 사용한다.
DB용 GCP storage·권한 계약이 없어 Gateway와 database의 조합은 schema가 거부한다.
기존 AWS 0.9.0, 레거시 on-prem 0.6.0, 등록 서버 0.8.0 pin은 각각 유지한다. 제품 Worker의 target
선택은 별도 저장소 연동 과제다. 공유 관리/ECR 장애는 새 배포/pull에 영향을 준다.
독립 serving은 AWS DB/API 없는 샘플에서 GCP Argo 연결만 끊어 HTTPS 지속을 확인한다.

dev zonal 구성은 HA가 아니며 node/NAT/LB/log/image 전송 비용을 배포 전에 확인한다.
GCP bootstrap/account와 수동 AWS gcp-access는 자동 apply에서 제외한다. GCP workload는
기본 비활성화된 독립 workflow에서 main의 검증된 변경만 fresh saved plan으로 적용한다.
GitHub CI WIF는 Argo와 pool을 분리하고 숫자 repository/owner ID와 정확한 main workflow를
검사한다. CI의 project IAM/SA/WIF 관리 권한 때문에 state/prefix 분리는 침해된 CI에 대한
보안 격리가 아니다. helper publisher는 별도 opt-in AWS role과 workflow를 사용하며
테스트와 Docker build 성공 후 immutable source SHA digest를 출력한다. GitOps/chart 게시를
자동 수행하지 않는다. 공유 helper main 변경은 기존 AWS apply를 유발할 수 있다.
구현 승인은 배포/push/apply 권한을 포함하지 않는다.
검증·복구: [GCP 런북](../runbooks/gcp-workload.md), [파이프라인](../runbooks/gcp-pipeline.md).
