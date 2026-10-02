# iris-platform

management EKS의 `iris-platform` namespace에 Iris control plane을 배포합니다. Argo CD Application `iris-platform`(AppProject `iris-platform-project`)이 동기화합니다.

`components.<키>`마다 리소스 `iris-<키>`를 만듭니다. 실행 방식과 버전을 다른 곳에서 정합니다.

| 무엇 | 어디 | 누가 |
| --- | --- | --- |
| 실행 방식: repository·command·port·ingress·SA·Secret·migration | `clusters/aws-dev-management/values/platform.yaml` (infra SHA 고정) | iris-infra PR |
| 버전: `components.<키>.digest` | `iris-gitops-environments/platform/aws-dev-management/<repo>.yaml` (main) | 각 서비스 레포의 수동 배포 workflow |

- digest가 없거나 iris-infra에 정의되지 않은 컴포넌트는 배포하지 않습니다(ServiceAccount만 만듭니다).
- `port`가 있으면 Service·`/healthz` probe, `ingress`면 baseline 앵커의 ALB group에 host 규칙, `allowedCidrs`면 ALB → 포트 NetworkPolicy, `migration`이면 같은 이미지의 PreSync Job, `argocdEgress`면 `argocd-server:8080` egress를 만듭니다. 모든 Pod는 RDS 서브넷 5432로 나갈 수 있습니다.
- 비밀값은 chart에 없고 Secret `iris-<키>-env`(또는 `secretName`)를 envFrom으로 읽습니다([deploy-platform runbook](../../../docs/runbooks/deploy-platform.md)).

현재 컴포넌트(iris-was, `was.yaml`):

| 키 | 리소스 | command | ServiceAccount | Secret |
| --- | --- | --- | --- | --- |
| `api` | Deployment·Service·Ingress `iris-api` + PreSync `iris-api-migration` | 이미지 CMD(:8000) / `alembic upgrade head` | `iris-api` | `iris-api-env` |
| `build-worker` | Deployment `iris-build-worker` | `python -m app.workers.build_worker` | `build-worker`(Pod Identity) | `iris-build-worker-env` |
| `deploy-worker` | Deployment `iris-deploy-worker` | `python -m app.workers.deploy_worker` | `deploy-worker` | `iris-deploy-worker-env` |
