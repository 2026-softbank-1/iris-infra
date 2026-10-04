# iris-service chart changelog

배포 순서: chart 변경이 main 에 들어가 merge commit 에 tag `iris-service-<version>` 이 생긴 뒤 `helm/gitops/values.yaml` 의 chartRevision 을 올립니다.

## 0.9.0

새 값은 모두 선택이고, 쓰지 않으면 0.7.1 과 렌더링이 같습니다(chart 버전 라벨만 다름).

- `projectId`: Pod 라벨 `iris.io/project-id`, NetworkPolicy `allow-project-egress`(같은 프로젝트 Pod 로 namespace 를 넘어 모든 포트).
- `service.exposeContainerPort`: app Service 에 `container`(containerPort) 포트 추가.
- `hostAliases`: ExternalName Service 로 compose 호스트명 별칭(`app.svc-{id}.svc.cluster.local` 만).
- `workload.kind: database` + `database.*`: 고정 공식 이미지(postgres·mysql·mongodb·redis, digest 필수) StatefulSet·PVC(1–20Gi, gp3),
  같은 프로젝트에서만 받는 `allow-project-ingress`, 서비스 삭제 시 PVC 삭제.
- `database.initScripts`: 최대 20개 `{name, content|binaryContent}` -> ConfigMap `app-initdb`(data/binaryData), StatefulSet `/docker-entrypoint-initdb.d` readOnly mount.
  이름 `NN-*.{sql,sql.gz,js}`, postgres·mysql 은 sql/sql.gz, mongodb 는 js, redis 는 거절. 데이터 디렉터리가 비어 있는 첫 기동에서만 실행(내용을 바꿔도 재실행 없음). 쓰지 않으면 렌더링 변화 없음.
- schema: top-level 필수는 `release` 만이고, app 은 `image`·`containerPort`·`health`·`route.host/className`, database 는 `database`·`projectId` 가 필수입니다.

## 0.8.0

- `imagePullSecrets`: Pod spec 에 그대로 넣습니다(사용자가 등록한 온프레미스 서버의 ECR pull Secret `iris-ecr-pull`). 0.9.0 부터 database StatefulSet 도 따릅니다.

## 0.7.1

- Pod 종료 대기: preStop sleep 15초, terminationGracePeriodSeconds 45.

## 0.7.0

- Deployment → Argo Rollouts Rollout, `deploymentStrategy`(ROLLING·CANARY·BLUE_GREEN).

## 0.6.0

- Deployment 기반 마지막 버전(on-prem 고정).
