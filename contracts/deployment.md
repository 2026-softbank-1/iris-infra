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
| `command` | | 이미지 ENTRYPOINT·CMD 를 exec form 으로 덮어씀(Dockerfile 빌드의 startCommand) |
| `health.path` | | 있으면 readiness `httpGet`(Host = `route.host`)·ALB health check 경로, 없으면 readiness TCP·ALB `/`. ALB 는 Host 를 못 넣어 항상 200-499 를 정상으로 봅니다 |
| `health.timeoutSeconds` | ✅ | 30~3600. Deployment `progressDeadlineSeconds` |
| `route.host` | ✅ | `{slug}.<BASE_DOMAIN>`. Ingress host, env `IRIS_PUBLIC_DOMAIN` |

## chart 기본값 (배포별 values 에 넣지 않음)

`replicas`, `resources`, `service.port`, `route.className`(`alb`)·`route.groupName`(`iris-svc-public`), `networkPolicy.allowedCidrs`.
타겟마다 다르면 ApplicationSet 의 Helm 값으로 덮어씁니다.

## 규칙

- namespace·release 이름은 ApplicationSet 이 `svc-{service_id}` 로 정합니다. 사용자 입력(slug)은 host 에만 씁니다.
- 실제 env 는 Git 에 넣지 않습니다. 사용자 변수 전달(`envSecretName` 등)은 변수 기능 구현 때 이 표에 추가합니다.
- 필드를 바꾸면 chart version 을 올리고, 백엔드(iris-was `render_service_values`)와 같은 PR 주기로 맞춥니다.
