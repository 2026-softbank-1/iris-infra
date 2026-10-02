# deployment

상태: 합의됨 (2026-10-02, [ADR 0002](../docs/decisions/0002-gitops-deployment.md)).

앱 values 검증 원본은 `helm/charts/iris-service/values.schema.json` 입니다. 모르는 키는 거절합니다.
값은 chart 기본값(`values.yaml`) → 배포별 values 순서로 합성합니다.

## 배포별 values (Deploy Worker 가 씀)

위치: `iris-gitops-environments/services/{service_id}/prod/values.yaml`. Deploy Worker 가 배포마다 파일 전체를 다시 씁니다.
내용은 JSON(YAML 의 부분집합)입니다.

| 필드 | 필수 | 의미 |
|---|---|---|
| `image.repository` | ✅ | ECR `iris/services/{service_id}` URI |
| `image.digest` | AWS ✅ | `sha256:<64 hex>`. 로컬 import 는 대신 `image.tag`(둘 중 하나만) |
| `release.id` | ✅ | Pod annotation `iris/release-id`. digest 가 같아도 release 마다 rollout |
| `release.sourceSha` | | env `IRIS_GIT_COMMIT_SHA` |
| `containerPort` | ✅ | 8080. env `PORT`, Service targetPort, probe 포트 |
| `environment` | | 공개 변수 `[{name,value}]` 또는 기존 Secret 참조 `[{name,valueFrom:{secretKeyRef:{name,key}}}]`. 최대 100개, 이름 중복 금지 |
| `command` | | 이미지 ENTRYPOINT·CMD 를 exec form 으로 덮어씀(Dockerfile 빌드의 startCommand) |
| `health.path` | | 있으면 readiness `httpGet`(Host = `route.host`)·ALB health check 경로, 없으면 readiness TCP·ALB `/`. ALB 는 Host 를 못 넣어 항상 200-499 를 정상으로 봅니다 |
| `health.timeoutSeconds` | ✅ | 30~3600. Deployment `progressDeadlineSeconds` |
| `route.host` | ✅ | `{slug}.<BASE_DOMAIN>`. Ingress host, env `IRIS_PUBLIC_DOMAIN` |

## chart 기본값 (배포별 values 에 넣지 않음)

`replicas`, `resources`, `service.port`, `route.className`(`alb`)·`route.groupName`(`iris-service-external`), `networkPolicy.allowedCidrs`.
타겟마다 다르면 ApplicationSet 의 Helm 값으로 덮어씁니다.

## 규칙

- namespace·release 이름은 ApplicationSet 이 `svc-{service_id}` 로 정합니다. 사용자 입력(slug)은 host 에만 씁니다.
- 비밀값 자체는 Git에 넣지 않습니다. `PASSWORD`, `SECRET`, `TOKEN`, `API_KEY`, `PRIVATE_KEY`, `DATABASE_URL`을 포함하는 변수는 `valueFrom.secretKeyRef`로만 전달합니다. Secret은 `svc-{service_id}` namespace에 미리 준비하며 차트는 생성하거나 읽지 않습니다.
- `PORT`, `IRIS_PUBLIC_DOMAIN`, `IRIS_GIT_COMMIT_SHA`는 플랫폼이 설정하므로 `environment`에서 덮어쓸 수 없습니다. 공개 값은 GitOps 이력에 남는 설정입니다.
- 필드를 바꾸면 chart version 을 올리고, 백엔드(iris-was `render_service_values`)와 같은 PR 주기로 맞춥니다.
