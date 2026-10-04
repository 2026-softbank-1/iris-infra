# iris-service

사용자 앱 공통 chart 입니다. Argo CD 가 Git tag `iris-service-<version>` 으로 읽고,
GitOps 저장소의 `services/{service_id}/prod/values.yaml`(Deploy Worker 작성)과 합쳐 렌더링합니다.

| 리소스 | 이름 | 내용 |
|---|---|---|
| Rollout(`argoproj.io/v1alpha1`) | `app` | `deploymentStrategy` 에 따른 배포 방식(아래), `automountServiceAccountToken: false`, readiness probe, Pod 종료 대기(preStop `sleep` 15초, `terminationGracePeriodSeconds` 45, 0.7.1). Argo Rollouts controller 가 필요합니다 |
| Deployment(`apps/v1`) | `app` | GCP `route.mode=gateway` 전용. ROLLING만 지원하며 Rollouts controller가 필요하지 않습니다 |
| Service | `app` | ClusterIP `service.port` → `http`(containerPort) |
| Ingress | `app` | `route.className: alb` 면 ALB group 공유·target-type ip·HTTPS redirect·health check 설정 |
| NetworkPolicy | `allow-load-balancer` | `networkPolicy.allowedCidrs` 가 있을 때만. 그 CIDR 에서 containerPort 로만 허용 |
| SealedSecret | `variables.name` | `variables` 가 있을 때만. sync-wave -1. Sealed Secrets controller 가 같은 이름의 Secret 으로 풉니다 |
| NetworkPolicy | `restrict-egress` | `networkPolicy.egressDeniedCidrs`(기본 VPC·link-local) 를 막고 같은 namespace·DNS·VPC 밖·`egressAllowed`(cidr·port)만 허용 |
| NetworkPolicy | `allow-project-egress` | `projectId` 가 있고 `restrict-egress` 가 있을 때(0.9.0). 라벨 `iris.io/project-id` 가 같은 Pod 로 namespace 를 넘어 모든 포트 egress 허용 |
| NetworkPolicy | `allow-project-ingress` | `workload.kind: database` 일 때(0.9.0). 같은 `projectId` Pod 에서만 ingress 허용, 그 외 차단 |
| Service(ExternalName) | `hostAliases[].name` | `hostAliases` 가 있을 때(0.9.0). `{name}` → `target`(`app.svc-{id}.svc.cluster.local`) |
| StatefulSet | `app` | `workload.kind: database` 일 때(0.9.0) Rollout·Ingress 대신. 아래 [데이터베이스](#데이터베이스-workloadkind-database-090) |

`imagePullSecrets` 가 있으면 Pod spec 에 넣습니다(0.8.0, 0.9.0 부터 database StatefulSet 도). 앱은 `registryPull.enabled=true`의 Secret과 중복 없이 합칩니다. 사용자가 등록한 온프레미스 서버는 ECR pull Secret `iris-ecr-pull`을 씁니다.

컨테이너는 `variables` 가 있으면 그 Secret 을 `envFrom` 으로 읽고, `iris.*` 가 있으면 `IRIS_SERVICE_NAME`·`IRIS_TARGET_NAME`·`IRIS_DEPLOYMENT_ID` env 를 갖습니다. env 가 envFrom 보다 우선합니다([ADR 0004](../../../docs/decisions/0004-user-variables-sealed-secrets.md)).

## 배포 방식 (`deploymentStrategy`)

AWS 타깃에서 Deploy Worker 가 `ROLLING`·`CANARY`·`BLUE_GREEN` 중 하나를 넘깁니다. on-prem 타깃은 Argo Rollouts 가 없어 0.6.0(Deployment, 롤링만)에 고정돼 있고 이 키를 받지 않습니다. 키가 없으면 `ROLLING` 입니다. 단계와 대기 시간은 chart 가 정하고 values 로 바꿀 수 없습니다.
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
- 블루그린으로 렌더링할 때만 Ingress 에 ALB health check 를 빠르게 하는 annotation(interval 5·timeout 4·healthy threshold 2)을 붙입니다. 전환 직후 새 target 이 첫 health check 를 통과하기까지의 공백을 줄이며, 롤링·카나리 TargetGroup 은 바뀌지 않습니다.

Pod 라벨 `iris/release-id` 는 로그·메트릭 수집(OTel → Loki `iris_release_id`)에 씁니다. selector 에는 없습니다.

- values 계약과 필드 의미: [contracts/deployment.md](../../../contracts/deployment.md). schema 가 모르는 키를 거절합니다.
- `ci/` 의 values 는 Deploy Worker 출력(AWS)과 로컬 k3d 모양입니다. `make helm-check` 가 lint·render 에 씁니다.
- 바꾸면 `Chart.yaml` version 을 올려 merge 하고, merge commit 에 tag `iris-service-<version>` 을 만든 뒤 별도 PR 로 `helm/gitops/values.yaml` `services.chartRevision` 을 올립니다. tag 보다 chartRevision 이 먼저 main 에 들어가면 모든 서비스 sync 가 실패합니다.
- namespace 에 `elbv2.k8s.aws/pod-readiness-gate-inject: enabled` 라벨이 있어야 rollout 이 ALB target health 를 기다립니다(ApplicationSet `managedNamespaceMetadata`). 롤링·카나리는 새 Pod 가 Service 에 바로 잡혀 이 gate 를 받고, 블루그린의 새 묶음은 전환 전까지 Service 밖이라 gate 를 받지 않습니다.

## 프로젝트 내부 통신 (0.9.0)

같은 프로젝트의 서비스는 namespace(`svc-{id}`)가 달라도 서로 부를 수 있어야 합니다. `restrict-egress` 는 VPC(Pod IP 포함)를 막으므로
0.7.x 까지는 다른 namespace Pod 로 나갈 수 없었습니다.

- `projectId`(문자열 또는 정수): Pod 라벨 `iris.io/project-id`. selector 에는 넣지 않습니다(Rollout·StatefulSet selector 는 바꿀 수 없습니다).
  `allow-project-egress` 가 같은 라벨 Pod 로의 egress 를 모든 포트로 엽니다. NetworkPolicy 는 합집합이라 `restrict-egress` 의 VPC 차단에 대한 예외가 됩니다.
  `restrict-egress` 가 없으면(egressDeniedCidrs 가 빈 값) egress 가 이미 열려 있어 만들지 않습니다.
- 앱 Pod 의 ingress 는 0.7.x 그대로 열려 있습니다(ALB 가 Pod IP 로 보냄). 다른 프로젝트 Pod 는 자기 `restrict-egress` 때문에 나오지 못합니다.
  데이터베이스만 `allow-project-ingress` 로 받는 쪽에서도 막습니다.
- `service.exposeContainerPort: true`: app Service 에 `name: container, port: containerPort` 를 더합니다. compose 처럼 `api:3000` 으로 부를 수 있게 합니다.
  `containerPort` 가 `service.port`(80)와 같으면 이미 열려 있어 더하지 않습니다.
- `hostAliases: [{name, target}]`: 이 namespace 에 ExternalName Service `{name}` 을 만들어 `{name}` → `target` CNAME 으로 풉니다.
  앱 코드의 `postgres:5432`·`api:3000` 이 코드 수정 없이 `app.svc-{id}.svc.cluster.local` 로 갑니다. 포트는 대상 app Service 의 포트입니다
  (데이터베이스는 `database.port`, 앱은 `exposeContainerPort` 의 containerPort).
  - `name`: DNS-1035 label(소문자 시작, 소문자·숫자·`-`, 63자), `app` 금지, 중복 금지, 20개까지. compose 서비스 이름이 이 규칙에 맞지 않으면(밑줄·대문자) Deploy Worker 가 넣지 않습니다.
  - `target`: `app.svc-{id}.svc.cluster.local` 만 허용합니다. 외부 도메인이나 다른 namespace 의 Service 로의 ExternalName 은 만들 수 없습니다.
    같은 프로젝트인지는 WAS 가 확인하고, 다른 프로젝트를 가리켜도 NetworkPolicy 가 막습니다.
  - ExternalName Service 는 ClusterIP 가 없어 docker-link env(`POSTGRES_PORT` 등)를 만들지 않습니다.

## 데이터베이스 (`workload.kind: database`, 0.9.0)

개발·데모용 단일 인스턴스입니다. 복제·백업이 없고, 서비스를 지우면 데이터도 지워집니다. 빌드가 없어 `image`·`command` 는 거절하고 `database.image` 를 씁니다.

| 키 | 필수 | 의미 |
|---|---|---|
| `workload.kind` | ✅ | `database` |
| `projectId` | ✅ | 같은 프로젝트 Pod 만 접속할 수 있게 합니다 |
| `database.engine` | ✅ | `postgres`·`mysql`·`mongodb`·`redis` |
| `database.image` | ✅ | 엔진별 Docker 공식 이미지(`[docker.io/][library/]{postgres,mysql,mongo,redis}[:tag]@sha256:…`)만, digest 필수 |
| `database.storage` | | `1Gi`~`20Gi`, 기본 `5Gi`. **만든 뒤에는 바꿀 수 없습니다**(StatefulSet volumeClaimTemplates 불변 — 바꾸면 Argo sync 가 실패) |
| `database.port` | | app Service 포트. 기본은 엔진 포트(5432·3306·27017·6379). 컨테이너는 엔진 기본 포트로 듣습니다 |
| `database.initScripts` | | 초기화 스크립트 최대 20개 `[{name, content \| binaryContent}]`. 아래 [초기화 스크립트](#초기화-스크립트-databaseinitscripts) |
| `database.storageClassName`·`database.resources` | | chart·타깃 기본값(gp3, 요청 100m/256Mi·제한 1CPU/1Gi). Deploy Worker 는 넣지 않습니다 |
| `variables` | | 자격 증명. 엔진 공식 env(`POSTGRES_USER/PASSWORD/DB`, `MYSQL_*`, `MONGO_INITDB_ROOT_*`, `REDIS_PASSWORD`)를 `envFrom` 으로 받습니다 |

`route`·`health`·`deploymentStrategy`·`service.exposeContainerPort`·`iris`·`release.sourceSha` 는 받아들이되 쓰지 않습니다. `replicas` 는 0(정지) 또는 1 입니다.

- StatefulSet `app`(serviceName `app`), volumeClaimTemplate `data`(RWO, `storageClassName`). `persistentVolumeClaimRetentionPolicy.whenDeleted: Delete` 라
  Argo 가 StatefulSet 을 지우면(서비스 삭제) PVC 와 볼륨(gp3 reclaimPolicy Delete)도 지워집니다. 0 으로 줄일 때는 남깁니다.
- Service `app`: `name: db, port: database.port → targetPort db`. Ingress 와 `allow-load-balancer` 는 없습니다.
- 보안: `runAsNonRoot`, 엔진 사용자 uid(postgres alpine 70, 그 밖 999)로 바로 실행, `fsGroup` 같은 값(`OnRootMismatch`), `seccompProfile: RuntimeDefault`,
  `allowPrivilegeEscalation: false`, capabilities 모두 drop, `automountServiceAccountToken: false`, `enableServiceLinks: false`. Pod Security restricted 도 만족합니다(svc-* 는 baseline).
- 엔진별: postgres `PGDATA=/var/lib/postgresql/data/pgdata`(볼륨 루트의 lost+found 회피), mysql `--datadir=/var/lib/mysql/data`, mongodb `/data/db`,
  redis `/data` + `--appendonly yes` + `REDIS_PASSWORD` 가 있으면 `--requirepass`(공식 entrypoint 처럼 `--protected-mode no`).
- probe: TCP. startup 5초×60(첫 초기화 동안 엔진은 TCP 를 열지 않음), readiness 5초, liveness 10초×6.
- Pod 라벨 `iris/release-id` 가 release 마다 바뀌어 배포마다 Pod 가 다시 뜹니다(볼륨은 유지). Argo health 는 StatefulSet Ready 로 판정합니다.

### 초기화 스크립트 (`database.initScripts`)

compose 의 `./db:/docker-entrypoint-initdb.d` 와 같은 효과입니다. 값이 있을 때만 ConfigMap `app-initdb` 를 만들고 StatefulSet 에 `/docker-entrypoint-initdb.d` 로 readOnly mount 합니다(없으면 렌더링이 이전과 같습니다).

```yaml
database:
  engine: postgres
  image: postgres:16-alpine@sha256:…
  initScripts:
    - name: 00-schema.sql          # content -> ConfigMap data
      content: "create table jobs (id serial primary key);\n"
    - name: 01-seed.sql.gz         # binaryContent(base64) -> ConfigMap binaryData
      binaryContent: H4sI…
```

- `name`: `^[0-9]{2}-[A-Za-z0-9._-]+\.(sql|sql\.gz|js)$`. 공식 이미지가 이름순으로 실행하므로 앞의 두 자리가 순서입니다. `content` 와 `binaryContent` 중 정확히 하나, 최대 20개.
- 엔진별 확장자: postgres·mysql 은 `.sql`·`.sql.gz`, mongodb 는 `.js`. `.sh` 는 받지 않습니다. redis 는 스크립트를 넣으면 schema 가 거절합니다(빈 목록은 허용).
- **첫 기동에서만 실행됩니다.** 공식 이미지는 데이터 디렉터리가 비어 있을 때만 이 디렉터리를 실행하므로, 스크립트 내용을 바꾸거나 Pod/ConfigMap 이 다시 떠도 이미 초기화된 PVC 에는 다시 실행되지 않습니다. 다시 실행하려면 서비스(PVC)를 지우고 새로 만듭니다.
- 스크립트 하나가 실패하면 엔진이 초기화 중 종료해 Pod 가 CrashLoop 됩니다. ConfigMap 은 1 MiB 제한이라 합계가 그 안이어야 합니다(schema 는 항목당 1 MiB 만 막습니다. 합계 검사는 Deploy Worker 가 합니다).
- 파일은 mode 0444 로 mount 되어 non-root uid 가 읽고, mongodb 는 `.js` 를 `mongosh` 로, postgres 는 `psql`, mysql 은 `mysql`(`.sql.gz` 는 압축을 풀어) 로 실행합니다.


## GCP 0.10.0 옵션

Ingress 경로의 Rollout과 기존 AWS `0.9.0`, 레거시 on-prem `0.6.0`, 등록 서버
`0.8.0` pin은 유지합니다. GCP는 새 `0.10.0` tag를 사용하고 `route.mode=gateway`, GCP parent Gateway/baseDomain,
`registryPull.enabled=true`, `tenantBudget.enabled=true`를 opt-in합니다.
`workload.kind`는 `app`만 지원합니다. GCP는 DB용 storage·권한 계약이 없어
`route.mode=gateway`와 `workload.kind=database`의 조합을 schema가 거부합니다.
Gateway 경로는 Deployment의 RollingUpdate(maxSurge 1·maxUnavailable 0)로 렌더링합니다.
`deploymentStrategy`는 생략하거나 `ROLLING`만 허용하며 `CANARY`·`BLUE_GREEN`은 거부합니다.
Deployment는 진행 기한 초과를 표시하지만 Rollout처럼 자동 abort하지 않습니다.
HTTPRoute와 같은 Service를 대상으로 한 HealthCheckPolicy를 생성하며 health는
인증 없이 HTTP 200이어야 합니다. `iris-ecr-pull`은 변수 Secret 이름으로 예약됩니다.
GCP Application은 credential data/expiry만 ignore하고 나머지는 계속 관리합니다.
자세한 [handoff](../../../contracts/gcp-target.md), [런북](../../../docs/runbooks/gcp-workload.md).
