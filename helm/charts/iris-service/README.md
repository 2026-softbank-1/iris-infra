# iris-service

사용자 앱 공통 chart 입니다. Argo CD 가 Git tag `iris-service-<version>` 으로 읽고,
GitOps 저장소의 `services/{service_id}/prod/values.yaml`(Deploy Worker 작성)과 합쳐 렌더링합니다.

| 리소스 | 이름 | 내용 |
|---|---|---|
| Rollout(`argoproj.io/v1alpha1`) | `app` | `deploymentStrategy` 에 따른 배포 방식(아래), `automountServiceAccountToken: false`, readiness probe. Argo Rollouts controller 가 필요합니다 |
| Service | `app` | ClusterIP `service.port` → `http`(containerPort) |
| Ingress | `app` | `route.className: alb` 면 ALB group 공유·target-type ip·HTTPS redirect·health check 설정 |
| NetworkPolicy | `allow-load-balancer` | `networkPolicy.allowedCidrs` 가 있을 때만. 그 CIDR 에서 containerPort 로만 허용 |
| SealedSecret | `variables.name` | `variables` 가 있을 때만. sync-wave -1. Sealed Secrets controller 가 같은 이름의 Secret 으로 풉니다 |
| NetworkPolicy | `restrict-egress` | `networkPolicy.egressDeniedCidrs`(기본 VPC·link-local) 를 막고 같은 namespace·DNS·VPC 밖·`egressAllowed`(cidr·port)만 허용 |

컨테이너는 `variables` 가 있으면 그 Secret 을 `envFrom` 으로 읽고, `iris.*` 가 있으면 `IRIS_SERVICE_NAME`·`IRIS_TARGET_NAME`·`IRIS_DEPLOYMENT_ID` env 를 갖습니다. env 가 envFrom 보다 우선합니다([ADR 0004](../../../docs/decisions/0004-user-variables-sealed-secrets.md)).

## 배포 방식 (`deploymentStrategy`)

Deploy Worker 가 `ROLLING`·`CANARY`·`BLUE_GREEN` 중 하나를 넘깁니다. 키가 없으면 `ROLLING` 입니다. 단계와 대기 시간은 chart 가 정하고 values 로 바꿀 수 없습니다.
`replicas` 가 2 미만이면 어떤 값이든 `ROLLING` 으로 렌더링합니다. 실제로 렌더링한 방식은 Rollout annotation `iris/deployment-strategy` 에 남습니다.

| 방식 | Rollout strategy | 동작 |
|---|---|---|
| `ROLLING` | `canary`(steps 없음), maxSurge 1·maxUnavailable 0 | 0.6.0 Deployment 의 RollingUpdate 와 같습니다 |
| `CANARY` | `canary` + `setWeight: floor(100/replicas)` → `pause: 60s` | 새 Pod 1개가 Ready 가 되면 이전 Pod 1개를 내리고 60초 봅니다. 그 뒤 나머지를 maxSurge 1·maxUnavailable 0 으로 바꿉니다. traffic routing 이 없어 트래픽은 Pod 수 비율로 나뉩니다 |
| `BLUE_GREEN` | `blueGreen`, `activeService: app`, autoPromotionSeconds 30, scaleDownDelaySeconds 30 | 새 묶음을 replicas 만큼 모두 Ready 로 띄운 뒤 30초 후 Service selector 를 새 묶음으로 바꾸고, 이전 묶음은 30초 뒤 내립니다. 배포 중 Pod 는 최대 2배입니다. preview Service 는 없습니다 |

- 공통: `progressDeadlineSeconds` = `health.timeoutSeconds`, `progressDeadlineAbort: true`. 기한 안에 진행하지 못하면 abort 해 이전 Pod 로 돌아가고 Argo health 가 `Degraded` 가 됩니다. pause 중(Argo health `Suspended`)에는 기한을 세지 않습니다.
- 카나리 weight 는 controller(v1.10.0)가 `replicas`·`replicas+1` 중 가장 가까운 Pod 수로 맞추는 규칙에서 새 Pod 가 정확히 1개가 되는 값입니다. `make helm-check` 가 같은 계산으로 replicas 2~10 을 검사합니다.
- 블루그린은 Service `app` 의 selector 에 `rollouts-pod-template-hash` 를 controller 가 덧붙입니다. Argo CD 는 이 필드를 직접 쓰지 않아 OutOfSync 가 되지 않습니다. 다른 방식으로 바꾸면 controller 가 그 selector 를 지웁니다.
- 제약: 롤링·카나리에서 블루그린으로 바꾼 뒤 **첫** 블루그린 배포는 Service 에 hash selector 가 없어, 새 Pod 가 Ready 가 되는 대로 트래픽을 받습니다(controller 의 fast-track). 두 번째 배포부터 전환 전까지 이전 묶음만 트래픽을 받습니다. AWS ALB 와의 관계는 [runbook](../../../docs/runbooks/argo-rollouts.md#블루그린과-aws-alb) 을 봅니다.

Pod 라벨 `iris/release-id` 는 로그·메트릭 수집(OTel → Loki `iris_release_id`)에 씁니다. selector 에는 없습니다.

- values 계약과 필드 의미: [contracts/deployment.md](../../../contracts/deployment.md). schema 가 모르는 키를 거절합니다.
- `ci/` 의 values 는 Deploy Worker 출력(AWS)과 로컬 k3d 모양입니다. `make helm-check` 가 lint·render 에 씁니다.
- 바꾸면 `Chart.yaml` version 을 올려 merge 하고, merge commit 에 tag `iris-service-<version>` 을 만든 뒤 별도 PR 로 `helm/gitops/values.yaml` `services.chartRevision` 을 올립니다. tag 보다 chartRevision 이 먼저 main 에 들어가면 모든 서비스 sync 가 실패합니다.
- namespace 에 `elbv2.k8s.aws/pod-readiness-gate-inject: enabled` 라벨이 있어야 rollout 이 ALB target health 를 기다립니다(ApplicationSet `managedNamespaceMetadata`). 롤링·카나리는 새 Pod 가 Service 에 바로 잡혀 이 gate 를 받고, 블루그린의 새 묶음은 전환 전까지 Service 밖이라 gate 를 받지 않습니다.
