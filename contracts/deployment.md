# deployment

상태: 합의됨 (2026-10-02, [ADR 0002](../docs/decisions/0002-gitops-deployment.md)).

앱 values 검증 원본은 `helm/charts/iris-service/values.schema.json` 입니다. 모르는 키는 거절합니다.
값은 chart 기본값(`values.yaml`) → 배포별 values 순서로 합성합니다.

## 배포별 values (Deploy Worker 가 씀)

위치: `iris-gitops-environments/services/{service_id}/{prod|onprem|onprem-{serverKey}}/values.yaml`. Deploy Worker 가 배포마다 파일 전체를 다시 씁니다.
내용은 JSON(YAML 의 부분집합)입니다.

| 필드 | 필수 | 의미 |
|---|---|---|
| `image.repository` | ✅ | ECR `iris/services/{service_id}` URI |
| `image.digest` | AWS ✅ | `sha256:<64 hex>`. 로컬 import 는 대신 `image.tag`(둘 중 하나만) |
| `release.id` | ✅ | Pod annotation `iris/release-id`. digest 가 같아도 release 마다 rollout |
| `release.sourceSha` | | env `IRIS_GIT_COMMIT_SHA` |
| `containerPort` | ✅ | 8080. env `PORT`, Service targetPort, probe 포트 |
| `command` | | 이미지 ENTRYPOINT·CMD 를 exec form 으로 덮어씀(Dockerfile 빌드의 startCommand) |
| `health.path` | | 있으면 readiness `httpGet`(Host = `route.host`)·ALB health check 경로, 없으면 readiness TCP·ALB `/`. ALB 는 Host 를 못 넣어 항상 200-499 를 정상으로 봅니다 |
| `health.timeoutSeconds` | ✅ | 30~3600. Rollout `progressDeadlineSeconds`(넘기면 abort) |
| `deploymentStrategy` | | `ROLLING`(기본)·`CANARY`·`BLUE_GREEN`. chart 0.7.0 부터. replicas 가 2 미만이면 chart 가 `ROLLING` 으로 렌더링합니다. 이전 chart schema 는 이 키를 거절하므로 0.7.0 을 쓰는 타깃(AWS)에만 씁니다. **on-prem 타깃은 `iris-service-0.6.0`(롤링만)이라 쓰지 않습니다** |
| `route.host` | ✅ | `{slug}.<BASE_DOMAIN>`. Ingress host, env `IRIS_PUBLIC_DOMAIN` |
| `iris.serviceName` | | env `IRIS_SERVICE_NAME`. 서비스 이름(DNS label) |
| `iris.targetName` | | env `IRIS_TARGET_NAME`. 배포 타깃 이름(예: `aws`) |
| `iris.deploymentId` | | env `IRIS_DEPLOYMENT_ID`. 앱을 띄운 배포 요청 id(정수) |
| `imagePullSecrets` | | `[{name}]`(DNS label, 1~5개). Pod spec `imagePullSecrets`. chart 0.8.0 부터. 사용자가 등록한 온프레미스 서버 타깃(`services/{id}/onprem-{serverKey}`)만 `[{name: iris-ecr-pull}]` 을 씁니다. 이전 chart schema 는 이 키를 거절합니다 |
| `variables.name` | 변수가 있을 때 | SealedSecret·Secret 이름(DNS label). release 마다 새 이름을 쓴다(예: `vars-r{release_id}`) |
| `variables.encryptedData` | 변수가 있을 때 | `{변수 이름: 봉인한 값(base64)}`. 이름은 영문·숫자·밑줄, `PORT`·`IRIS_*` 는 거절, 1~100개 |
| `projectId` | DB ✅ | chart 0.9.0 부터. Pod 라벨 `iris.io/project-id`(문자열 또는 정수). 같은 값의 Pod 끼리 namespace 를 넘어 통신(`allow-project-egress`) |
| `service.exposeContainerPort` | | chart 0.9.0 부터. `true` 면 app Service 에 containerPort 와 같은 포트(`container`)를 더 엽니다 |
| `hostAliases` | | chart 0.9.0 부터. `[{name, target}]`. name 은 DNS-1035 label(`app` 금지, 중복 금지, 20개까지), target 은 `app.svc-{id}.svc.cluster.local` 만 |
| `workload.kind` | | chart 0.9.0 부터. `app`(기본)·`database` |
| `database.engine`·`database.image` | DB ✅ | `postgres`·`mysql`·`mongodb`·`redis`, 이미지는 엔진별 Docker 공식 리포지토리 + `@sha256:` digest 필수 |
| `database.storage`·`database.port` | | `1Gi`~`20Gi`(기본 5Gi, 만든 뒤 변경 불가), Service 포트(기본 엔진 포트) |
| `database.initScripts` | | chart 0.9.0 부터. `[{name, content\|binaryContent}]` 최대 20개, name `^[0-9]{2}-[A-Za-z0-9._-]+\.(sql\|sql\.gz\|js)$`(postgres·mysql: sql/sql.gz, mongodb: js, redis: 거절). ConfigMap `app-initdb` -> `/docker-entrypoint-initdb.d`, **데이터 디렉터리가 비어 있는 첫 기동에서만 실행** |

`workload.kind: database` 면 `image`·`command` 를 거절하고 `projectId`·`database` 가 필수입니다. `route`·`health`·`containerPort`·`deploymentStrategy`·`iris`
는 받아들이되 쓰지 않습니다. 자격 증명은 `variables`(엔진 공식 env 이름)로 넣습니다. 0.9.0 키는 이전 chart schema 가 거절하므로 0.9.0 을 쓰는 타깃(AWS)에만 씁니다.
자세한 렌더링은 [chart README](../helm/charts/iris-service/README.md#프로젝트-내부-통신-080) 를 봅니다.

## chart 기본값 (배포별 values 에 넣지 않음)

`replicas`, `resources`, `service.port`, `database.storageClassName`·`database.resources`, `route.className`(`alb`)·`route.groupName`(`iris-service-external`), `networkPolicy.allowedCidrs`·`egressDeniedCidrs`·`egressAllowed`.
타겟마다 다르면 ApplicationSet 의 Helm 값으로 덮어씁니다.
chart 버전도 타깃마다 고정합니다: AWS `services.chartRevision`(현재 `iris-service-0.7.1`, Rollout), on-prem `services.onprem.chartRevision`(`iris-service-0.6.0`, Deployment·롤링만).

## 규칙

- namespace·release 이름은 ApplicationSet 이 `svc-{service_id}` 로 정합니다. 사용자 입력(slug)은 host 에만 씁니다.
- 사용자 변수는 평문으로 Git 에 넣지 않습니다([ADR 0004](../docs/decisions/0004-user-variables-sealed-secrets.md)). Deploy Worker 가 workload 의 Sealed Secrets controller 공개 인증서로 변수마다 봉인해(strict scope: namespace `svc-{service_id}`, name = `variables.name`) `variables.encryptedData` 에 쓰고, chart 가 `SealedSecret`(sync-wave -1)과 컨테이너 `envFrom` 을 만듭니다. 값을 못 풀면 SealedSecret 이 Degraded 가 되어 Pod 이 새로 뜨지 않습니다.
- `env`(`PORT`·`IRIS_*`)가 `envFrom` 보다 우선합니다. 사용자 변수는 그 이름을 덮어쓰지 못하고 schema 도 그 이름을 거절합니다.
- 롤백은 GitOps revert commit 이라 이전 values 의 `variables` 가 그대로 돌아옵니다. 이름이 release 마다 달라 새 Secret 이 먼저 만들어지고, 이전 Secret 은 `PruneLast` 로 나중에 지워집니다.
- `iris`·`variables` 는 선택입니다. 없으면 해당 env·Secret 이 없는 0.5.0 때와 같은 렌더링입니다.
- 필드를 바꾸면 chart version 을 올리고, 백엔드(iris-was `render_service_values`)와 같은 PR 주기로 맞춥니다.
