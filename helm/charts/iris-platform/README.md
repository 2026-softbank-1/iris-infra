# iris-platform

management EKS의 `iris-platform` namespace에 Iris control plane을 배포합니다. Argo CD Application `iris-platform`(AppProject `iris-platform-project`)이 동기화합니다.

| 컴포넌트 | 리소스 | command | ServiceAccount | Secret(envFrom) |
| --- | --- | --- | --- | --- |
| Control API | Deployment·Service·Ingress `iris-api` | 이미지 기본 CMD(uvicorn :8000) | `iris-api` | `iris-api-env` |
| Build Worker | Deployment `iris-build-worker` | `python -m app.workers.build_worker` | `build-worker`(Pod Identity) | `iris-build-worker-env` |
| Deploy Worker | Deployment `iris-deploy-worker` | `python -m app.workers.deploy_worker` | `deploy-worker` | `iris-deploy-worker-env` |
| DB migration | Sync hook Job `iris-db-migration`(wave -1, api 이미지) | `alembic upgrade head` | `iris-api` | `iris-api-env` |

- 세 컴포넌트는 iris-was 이미지(`iris/was`) 하나를 쓰고 digest로만 지정합니다. **digest가 빈 컴포넌트는 배포하지 않습니다.**
- 값 합성: chart 기본값 → `clusters/aws-dev-management/values/platform.yaml`(infra SHA 고정) → `iris-gitops-environments/platform/aws-dev-management/values.yaml`(digest, main 추적, iris-was 수동 배포 workflow가 기록).
- DB는 foundation의 RDS입니다. 비밀값은 chart에 없고 운영자가 만든 Secret을 이름으로 참조합니다([deploy-platform runbook](../../../docs/runbooks/deploy-platform.md)).
- NetworkPolicy(baseline `application-boundary`에 추가): ALB 서브넷 → API 8000, 모든 platform Pod → RDS 서브넷 5432, Deploy Worker → `argocd-server` 8080.
- API Ingress는 baseline 앵커가 소유한 ALB group `iris-platform-external`에 host 규칙만 추가합니다.
