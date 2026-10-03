# iris-platform

Management EKS의 `iris-platform` namespace에 Iris 플랫폼을 배포하는 Chart입니다. Namespace·Quota·기본 NetworkPolicy·ALB 앵커는 `cluster-baseline`이 소유합니다. DB는 외부 RDS PostgreSQL이며 이 Chart는 DB·PVC·Secret을 생성하지 않습니다.

| 구성 요소 | 이미지 / 실행 | 네트워크·자격증명 |
| --- | --- | --- |
| API | `api.digest`, `uvicorn app.main:app --host 0.0.0.0 --port 8000` | ALB → ClusterIP 8000, DB Secret, `iris-platform-api` Pod Identity(빌드 로그 읽기, 소스 업로드 `uploads/*` 쓰기·`snapshots/*` 읽기) |
| Build Worker | `buildWorker.digest`, `python -m app.workers.build_worker` | `build-worker` Pod Identity, DB·Build GitHub App |
| Deploy Worker | `deployWorker.digest`, `python -m app.workers.deploy_worker` | `deploy-worker` Pod Identity, DB·별도 GitOps App·Argo reader token |
| Migration | `api.digest`, `alembic upgrade head` | 같은 DB Secret, Worker AWS 권한 없음 |
| Error Check Agent | `errorAgent.image.digest`, `python -m ai_error_check_agent.api --host 0.0.0.0 --port 8001` | 내부 ClusterIP 8001, LLM·Agent API key |

API와 Worker는 최초 replica 1입니다. Worker에는 존재하지 않는 HTTP health probe를 넣지 않습니다. Build Worker 종료 유예는 최소 120초입니다. API 종료 유예는 기본 30초이고 운영은 90초입니다(롤아웃과 겹친 AI 진단이 끝나도록 iris-was 의 종료 대기 60초보다 길게 둡니다). Error Agent는 기본 비활성화입니다. Code Analyzer는 운영 서버·이미지 준비 후 별도로 설계합니다.

**버전(digest)은 이 저장소에 두지 않습니다.** 각 서비스 레포의 수동 배포 workflow가 `iris-gitops-environments/platform/aws-dev-management/<repo>.yaml`에 컴포넌트별 digest만 커밋하고(`was.yaml`: `api`·`buildWorker`·`deployWorker`, `error-check-agent.yaml`: `errorAgent.image`), Argo CD Application `iris-platform`이 이 저장소의 values와 병합해 자동 sync합니다. digest가 없는 컴포넌트는 렌더링하지 않으므로 컴포넌트를 따로 배포할 수 있습니다. Error Agent는 `enabled`와 digest가 모두 있어야 배포됩니다.

## values

`values.yaml`의 빈 값은 배포 전 채워야 하는 입력입니다. `values.schema.json`은 알 수 없는 필드, tag 대신 digest 누락, 빈 hostname·Secret 참조·CIDR, 잘못된 ECR 경로 등을 거절합니다. 실제 Secret 값은 values에 넣지 않습니다. 실행 가능한 **가짜 검증 데이터**는 `ci/was-values.yaml`, `ci/error-agent-values.yaml`에 있습니다. 운영에는 `clusters/aws-dev-management/values/platform.yaml`을 채웁니다.

| 설정 | 필요한 값 |
| --- | --- |
| `was.image.repository` | `…amazonaws.com/iris/was` (digest는 GitOps 파일의 컴포넌트별 `digest`) |
| `api.host` | 운영자가 선택한 API FQDN; management 앵커 인증서 SAN과 일치 |
| `api.buildLogGroup` | (선택) foundation의 CodeBuild 로그 그룹(dev: `/aws/codebuild/iris-dev-build`). 있으면 API ConfigMap에 `AWS_REGION`·`BUILD_LOG_GROUP` 을 넣어 배포 상세의 빌드 로그를 읽는다. 비어 있으면 넣지 않는다 |
| `was.envSecret` | WAS `.env` 전체를 담은 Secret. API·Worker·migration에 `envFrom`으로 모든 키를 주입(명시 `env`가 우선) |
| `database.secret` | `DATABASE_URL`을 가진 Secret. `.env` Secret과 같은 이름이면 Secret 하나로 운영 |
| `database.caConfigMap`, `caKey` | RDS CA bundle을 보관한 기존 ConfigMap과 데이터 키 |
| `buildWorker.codebuildProject`, `artifactBucket` | foundation의 `build_codebuild_project_name`, `build_artifact_bucket_name` 출력 |
| `buildWorker.githubSecret` | 아래 Build GitHub App 키를 가진 기존 Secret |
| `deployWorker.baseDomain` | 사용자 서비스 도메인; `api.host`와 별도 설정 |
| `deployWorker.gitopsRepository` | `2026-softbank-1/iris-gitops-environments` (`owner/repo` 형식) |
| `deployWorker.githubSecret` | Build App과 다른 GitOps App Secret |
| `deployWorker.argocdUrl`, `argocdSecret` | HTTPS Argo CD URL, 읽기 전용 project token Secret |
| `deployWorker.caConfigMap`, `caKey` | Argo 내부 CA와 GitHub 등 공개 HTTPS CA를 포함한 완전한 bundle |
| `network.albSubnetCidrs` | management ALB가 위치한 public subnet CIDR 목록 |
| `network.rdsSubnetCidrs` | RDS subnet CIDR 목록; failover 가능한 모든 subnet 포함 |
| `errorAgent.enabled`, `image.repository`, `secret`, `model` | Agent 활성화 여부·ECR 저장소·아래 두 Secret 키·LLM 모델 (digest는 `error-check-agent.yaml`) |

모든 Secret·CA ConfigMap은 `iris-platform`에 별도 준비합니다.

| 기존 Secret | 필수 데이터 키 | 소비자 |
| --- | --- | --- |
| DB | `DATABASE_URL` (또는 `database.urlKey`) | API·두 Worker·migration Job |
| Build GitHub App | `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_PUBLIC_INSTALLATION_ID` | Build Worker만 |
| Deploy GitHub App | `GITOPS_APP_ID`, `GITOPS_APP_PRIVATE_KEY`, `GITOPS_INSTALLATION_ID` | Deploy Worker만 |
| Argo reader | `ARGOCD_TOKEN` | Deploy Worker만 |
| Error Agent | `LLM_API_KEY`, `AGENT_API_KEY` | Error Agent만 |

GitHub App private key는 PEM 원문이며 ID 값은 정수 문자열입니다. Agent API key는 공백 없는 ASCII 32자 이상이어야 합니다. Secret 전체 `envFrom` 대신 필요한 키만 주입합니다. API·migration·Agent SA에는 Worker Pod Identity 역할을 연결하지 않습니다. WAS는 UID/GID 1001, Agent 이미지는 숫자 non-root USER로 빌드해야 합니다.

## RDS TLS와 migration

DB URL은 `postgresql+asyncpg://<user>:<encoded-password>@<rds-endpoint>:5432/<database>` 형식입니다. 비밀번호 특수문자는 URL 인코딩하고 `ssl`, `sslmode`, `sslrootcert`, `sslcert`, `sslkey`, `sslcrl`, `sslpassword` query parameter를 넣지 않습니다. SQLAlchemy의 asyncpg 경로는 query를 연결 인자로 전달하므로 URL TLS 옵션과 환경변수를 섞지 않습니다.

API·Worker·Job은 `PGSSLMODE=verify-full`, `PGSSLROOTCERT=/etc/iris-rds/ca-bundle.pem`을 사용합니다. 동일 WAS digest의 initContainer가 URL 형식·TLS query 충돌·CA 파싱을 검사하고, 오류에는 URL·비밀번호를 출력하지 않습니다. 이 검사는 실제 DB 연결이나 사용자 권한을 검증하지 않습니다. RDS endpoint hostname을 사용합니다. 현재는 RDS master 계정 하나를 API·Worker·migration이 함께 씁니다. 계정을 runtime(DML)·migration(DDL)으로 나누려면 chart에 Secret 필드를 다시 분리해야 합니다.

GitOps digest 커밋이 들어오면 Argo가 **자동 sync**합니다: 준비 리소스(wave -2) → migration `Sync` hook(wave -1, `api.digest`가 있을 때) → Deployment(wave 0). migration은 첫 설치에서도 chart가 만든 RDS egress NetworkPolicy가 있어야 하므로 PreSync가 아니라 wave -1 Sync hook입니다. Job은 timeout/backoff가 있고 성공하면 삭제하며 실패하면 남깁니다. 재시도 시 이전 Job을 교체합니다. 선택적 resource sync는 hook을 건너뛰므로 release에 사용하지 않습니다.

## Ingress·CA·회전

API Ingress는 기존 `iris-platform-external` group에 host 규칙을 추가합니다. ALB 이름·scheme·certificate·redirect는 baseline 앵커가 소유합니다. `listen-ports`는 LBC에서 Ingress마다 적용되므로 API Ingress도 앵커와 같은 HTTP 80·HTTPS 443을 선언합니다(없으면 HTTPS 규칙이 생기지 않습니다). target type은 IP, Service/Pod는 8000, ALB health는 `/readyz`의 **204**입니다. Kubernetes startup/liveness는 `/healthz`, readiness는 `/readyz`입니다.

Chart의 NetworkPolicy는 baseline에 ALB→API 8000, DB client→RDS 5432, Deploy Worker→argocd-server Pod 8080, API→observability Loki 3100·Prometheus 9090(로그·메트릭 조회) 허용을 더합니다. 정책은 합산되며 기본 namespace 내부·DNS·외부 HTTPS 허용을 더 제한하지 않습니다. RDS SG, 라우팅과 실제 subnet CIDR도 별도로 맞춰야 합니다.

Deploy Worker는 `SSL_CERT_FILE=/etc/iris-argocd/ca-bundle.pem`으로 내부 Argo 인증서를 검증합니다. Argo URL hostname이 서버 인증서 SAN에 있어야 합니다. 공개 root CA를 빼면 같은 HTTP client의 GitHub 통신도 실패합니다. TLS 검증을 끄지 않습니다. Secret 변경은 checksum으로 감지하지 않으므로 회전 후 관련 Deployment를 재시작합니다. CA volume은 갱신되지만 기존 client/DB 연결은 CA를 다시 읽지 않을 수 있어 CA 회전 후에도 재시작합니다. 실패한 migration Job은 원인 해결 후 전체 sync로 재실행합니다.

배포·검증·복구 절차는 [platform runbook](../../../docs/runbooks/deploy-platform.md)을 참고합니다.
