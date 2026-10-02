# iris-service

사용자 앱 공통 chart 입니다. Argo CD 가 Git tag `iris-service-<version>` 으로 읽고,
GitOps 저장소의 `services/{service_id}/prod/values.yaml`(Deploy Worker 작성)과 합쳐 렌더링합니다.

| 리소스 | 이름 | 내용 |
|---|---|---|
| Deployment | `app` | rolling update(maxSurge 1·maxUnavailable 0), `automountServiceAccountToken: false`, readiness probe |
| Service | `app` | ClusterIP `service.port` → `http`(containerPort) |
| Ingress | `app` | `route.className: alb` 면 ALB group 공유·target-type ip·HTTPS redirect·health check 설정 |
| NetworkPolicy | `allow-load-balancer` | `networkPolicy.allowedCidrs` 가 있을 때만. 그 CIDR 에서 containerPort 로만 허용 |

- values 계약과 필드 의미: [contracts/deployment.md](../../../contracts/deployment.md). schema 가 모르는 키를 거절합니다.
- `ci/` 의 values 는 Deploy Worker 출력(AWS)과 로컬 k3d 모양입니다. `make helm-check` 가 lint·render 에 씁니다.
- 바꾸면 `Chart.yaml` version 을 올리고 tag 를 만든 뒤 ApplicationSet `targetRevision` 을 올립니다.
- namespace 에 `elbv2.k8s.aws/pod-readiness-gate-inject: enabled` 라벨이 있어야 rollout 이 ALB target health 를 기다립니다(ApplicationSet `managedNamespaceMetadata`).

`environment`는 분석 파이프라인에서 확인한 공개 값 또는 기존 namespace Secret 참조를 컨테이너에 전달합니다. 값·참조 중 하나만 허용하며 플랫폼 변수와 중복 이름은 거절합니다. Secret을 만드는 기능과 빌드 단계 Secret 전달은 포함하지 않습니다.
