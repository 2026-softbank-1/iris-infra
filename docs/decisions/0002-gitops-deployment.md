# 0002: GitOps(Argo CD) 배포와 chart 전달

상태: 채택 (2026-10-02). [0001](0001-repository-and-deployment.md)의 배포 방식(BuildKit Job + Worker 의 Helm 직접 호출)을 대체합니다.

## 배경

백엔드(iris-was)는 CodeBuild 로 이미지를 만들고, 배포 결과를 GitOps 저장소와 Argo CD 로 재현 가능하게 관리하기로 했습니다.
Worker 가 앱 EKS 에 Helm 을 직접 호출하면 Worker 에 클러스터 쓰기 권한이 필요하고, 배포 상태의 원본이 Git 에 남지 않습니다.

## 결정

- **배포**: Deploy Worker 는 `iris-gitops-environments` 의 `services/{service_id}/prod/values.yaml` 하나만 커밋합니다(`main` fast-forward, force push 금지).
  Argo CD ApplicationSet 이 `services/*/prod` 디렉터리마다 Application 을 만들고, `iris-service` chart 와 그 values 로 앱 EKS 에 동기화합니다.
- **chart 전달**: Argo CD 가 이 저장소의 `helm/charts/iris-service` 를 **Git tag `iris-service-<version>`** 로 직접 읽습니다(multi-source: chart + `$values`).
  OCI 패키징·ECR helm 저장소·Argo CD 의 ECR 토큰 갱신은 두지 않습니다. 로컬 CLI 가 같은 chart 를 OCI 로 받아야 할 때 추가합니다.
- **values 계약**: [contracts/deployment.md](../../contracts/deployment.md). 검증 원본은 chart 의 `values.schema.json` 이며 모르는 키를 거절합니다.
- **외부 트래픽**: ALB Ingress. 모든 서비스 Ingress 가 `alb.ingress.kubernetes.io/group.name` 으로 ALB 하나를 공유합니다. 인증서는 ACM 와일드카드를 host 로 자동 탐색합니다.

## 영향

- 앱 EKS Access Entry 는 Argo CD application-controller 역할에 줍니다. Deploy Worker 역할에는 앱 EKS 권한을 주지 않습니다.
- Argo CD 에 이 저장소의 읽기 전용 자격증명이 필요합니다. terraform 코드도 읽을 수 있으므로 비밀값을 커밋하지 않는 규칙을 유지합니다.
- chart 를 바꾸면 `Chart.yaml` version 을 올리고 같은 이름의 tag 를 만든 뒤, ApplicationSet 의 `targetRevision` 을 올립니다. 모든 서비스가 같은 버전을 씁니다.
- 같은 major 버전 안에서는 values 하위 호환을 지킵니다(필드 삭제·제약 강화 금지). rollback 은 이전 release 의 values 를 그대로 복원하므로, 깨면 rollback 이 schema 에 거부됩니다. 깨야 하면 새 major 와 이행 절차를 따로 둡니다.
- ALB 하나의 listener 규칙 수에 상한이 있습니다. 서비스가 늘면 quota 증설 또는 group 분할, Gateway API 전환을 검토합니다. values 의 `route.host` 는 구현 중립이라 Deploy Worker 는 바뀌지 않습니다.
- `examples/`·`clusters/*/values/service-defaults.yaml` 은 이전 초안 필드(`ingress.*`, `envSecretName`)를 담고 있어 이 계약과 맞지 않습니다. `make helm-check` 는 chart 의 `ci/` values 로 검증합니다.

## 검증 사항

- ApplicationSet multi-source(Git path chart + `$values`)가 tag 를 고정해 렌더링하는지, `status.sync.revisions` 순서가 `spec.sources` 와 같은지
- ALB group 공유·ACM 자동 탐색·`pod-readiness-gate-inject` 로 rollout 이 ALB target health 를 기다리는지
- VPC CNI NetworkPolicy(`networkPolicy.allowedCidrs`)가 ALB 트래픽만 허용하고 kubelet probe 를 막지 않는지
