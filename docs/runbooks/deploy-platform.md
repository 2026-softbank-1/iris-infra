# Iris 플랫폼 배포

`helm/charts/iris-platform`은 management EKS의 API·Build Worker·Deploy Worker와 선택적 Error Check Agent를 구현합니다. DB는 외부 RDS PostgreSQL입니다. 사용자 서비스는 별도 workload EKS에 배포됩니다. 플랫폼 Chart와 실행 방식 values는 이 인프라 저장소에, 컴포넌트별 이미지 digest는 `iris-gitops-environments/platform/aws-dev-management/<repo>.yaml`에 둡니다.

현재 코드 검증은 Helm lint/render, backend 없는 Terraform validate, 격리 mock test입니다. 실제 이미지 pull·RDS 연결·migration·Worker·인터넷 HTTPS 동작은 아래 운영 검증 전까지 확인된 것이 아닙니다. WAS의 조사한 커밋은 `/healthz`, `/readyz`만 제공하므로 이 Chart가 업무 API나 Agent 연동을 구현하지는 않습니다.

## 배포 전 입력과 선행 조건

1. [EKS 운영 경로](eks-access.md)로 management/workload, baseline, LBC, metrics, 관측 스택과 Argo를 먼저 준비합니다. `iris-platform` Namespace·Quota·기본 NetworkPolicy와 management ALB 앵커가 있어야 합니다.
2. 이미지는 각 서비스 레포의 수동 배포 workflow가 ECR에 게시하고 digest를 GitOps 파일에 커밋합니다(iris-was: API·Build Worker·Deploy Worker를 골라서, migration은 API digest). WAS 이미지의 실행 UID는 1001입니다. Error Agent 소스에는 아직 루트 Dockerfile이 없으므로 별도 저장소에서 dependencies·모듈·numeric non-root USER를 포함한 이미지를 준비한 뒤 활성화합니다. Code Analyzer는 후속 작업입니다.
3. RDS(foundation `database.tf`)의 master 계정으로 만든 `DATABASE_URL`을 Secret `database.secret`(`iris-platform-db`) 하나에 넣습니다. API·Worker·migration이 함께 씁니다.
4. API hostname을 선택하고 management ALB로 DNS를 연결합니다. `api.host`는 기존 baseline 앵커 ACM 인증서의 SAN과 맞아야 합니다. 새 도메인·새 인증서가 필요하면 DNS/ACM 작업을 따로 계획합니다. `deployWorker.baseDomain`은 사용자 서비스 wildcard 도메인입니다.
5. Build GitHub App과 **별도 Deploy GitHub App**의 ID·PEM·installation을 준비합니다. Deploy App은 `iris-gitops-environments`에 Contents read/write 권한이 있어야 합니다. 해당 저장소 main 보호는 force push·삭제를 금지하고 직접 커밋을 허용해야 합니다. PR 필수가 켜졌다면 승인된 GitHub App bypass 설정을 확인합니다.
6. Argo 저장소 읽기 자격증명은 비공개 `iris-infra`와 `iris-gitops-environments` 모두를 읽을 수 있어야 합니다. 현재 bootstrap은 제공한 같은 읽기 자격증명을 두 repository Secret에 등록합니다. Deploy Worker 쓰기 App과 Argo 읽기 credential을 분리합니다. repo 하나만 허용하는 SSH deploy key로 두 저장소를 읽을 수 없습니다.
7. Argo `iris-svc-project`의 `iris-deploy-reader` role로 Application 조회 token을 발급해 별도 Secret에 보관합니다. 이 role은 사용자 Application `get`만 허용합니다. URL은 HTTPS이며 서버 인증서 SAN에 일치하는 hostname이어야 합니다. 내부 Argo CA와 공개 root CA를 모두 포함한 bundle을 준비합니다.
8. [Chart의 Secret 표](../../helm/charts/iris-platform/README.md)에 따라 기존 Secret·CA ConfigMap을 `iris-platform`에 별도 생성합니다. 비밀값을 Git·values·쉘 로그에 기록하지 않습니다. DB Secret은 `database.secret` 하나를 API·Worker·migration이 함께 씁니다(현재 RDS master 계정). 두 GitHub App Secret 이름은 서로 달라야 합니다.

DB URL의 query에 TLS 옵션을 넣지 않습니다. Chart가 주입하는 `PGSSLMODE=verify-full`과 CA mount로 검증합니다. initContainer는 URL/TLS/CA 형식만 확인하며 실제 DB 연결은 migration과 운영 검사에서 확인합니다.

## IAM 적용 순서와 권한

Terraform 코드에는 foundation의 `deploy_worker_role_arn`과 management `iris-platform/deploy-worker` Pod Identity association이 있습니다. 신뢰 조건은 management 클러스터·namespace·SA 세 가지입니다. 역할은 `iris/services/*`의 `ecr:BatchGetImage`, `ecr:PutImage`만 허용해 정상 배포 이미지에 tag를 붙입니다. Build Worker 권한, CodeBuild 역할과 EKS Access Entry는 추가하지 않습니다.

실제 적용은 별도 승인을 받은 뒤 관리자 경로로 account CI policy를 먼저 적용하고 foundation, management 순서로 적용합니다. CI는 새 역할을 관리하지만 `iam:PassRole`은 `pods.eks.amazonaws.com`에만 허용합니다. 신규 output 없이 management를 먼저 적용하면 remote state 계약이 실패합니다. main push는 기존 CI의 foundation 자동 apply를 유발할 수 있으므로 코드 구현 승인만으로 실행하지 않습니다.

## 비밀값 없는 values와 정적 검사

`clusters/aws-dev-management/values/platform.yaml`에 ECR 저장소·hostname·Secret/CA 이름·CodeBuild/S3·Argo URL·CIDR가 채워져 있습니다. digest는 이 파일에 넣지 않습니다. 기본 `errorAgent.enabled=false`를 유지하고 이미지와 keys/model이 준비됐을 때만 켭니다. 가짜 `ci/*` 값은 운영에 쓰지 않습니다.

```sh
make scaffold-check
make helm-check
make tf-check
make tf-test
python3 scripts/tests/test-eks-ci-permissions.py
python3 scripts/tests/test-eks-ops.py
```

Helm 3.19.1, Terraform `.terraform-version`, PyYAML 6.0.3과 고정 Chart/provider 다운로드 경로가 필요합니다. 전체 lint/render는 아래 platform 검사도 포함합니다.

```sh
helm lint --strict helm/charts/iris-platform \
  -f clusters/aws-dev-management/values/platform.yaml --kube-version 1.35.0 --namespace iris-platform
helm template iris-platform helm/charts/iris-platform \
  -f clusters/aws-dev-management/values/platform.yaml --kube-version 1.35.0 --namespace iris-platform
```

렌더링 성공은 Secret 존재·권한·DB 연결·이미지 실행 성공을 보장하지 않습니다.

## Argo Application 활성화와 첫 sync

아래는 **운영자가 별도로 실행 승인을 받은 뒤** 사용하는 절차입니다. 기본 bootstrap은 platform Application을 만들지 않습니다. 이전에 bootstrap한 root의 source revision은 자동으로 최신 main을 따라가지 않으므로 새 immutable SHA로 갱신해야 새 Chart/Application 정의를 읽습니다.

1. 채운 values와 구현을 검토·병합한 immutable SHA를 준비하고 깨끗한 checkout에서 기존 bootstrap 필수 환경변수/credential/target 조건을 충족합니다.
2. `GITOPS_PLATFORM_ENABLED=1`로 bootstrap합니다. 이 플래그는 정확히 `0` 또는 `1`이며 기본은 `0`입니다. 활성화 시 platform Chart를 cluster values로 strict lint/render하고 두 target 선행 검사를 통과한 뒤 첫 클러스터 변경을 수행합니다.
3. root `iris-addons`는 Synced, 기존 8개 addon은 Synced/Healthy, 새 `iris-platform` Application은 존재하는 상태까지 기다립니다. bootstrap은 platform Healthy를 주장하거나 기다리지 않습니다. GitOps digest가 없으면 SA·ConfigMap·NetworkPolicy만 생깁니다.
4. 서비스 레포의 **Deploy platform** workflow를 실행합니다(iris-was는 API부터). digest 커밋을 Argo가 감지(약 3분)해 자동 sync합니다. 실패한 hook을 선택적 sync로 우회하지 않습니다.

```sh
# 기존 bootstrap 필수 환경변수는 eks-access runbook 참조
GITOPS_PLATFORM_ENABLED=1 make bootstrap CLUSTER=aws-dev-management
# 이후 배포는 iris-was Actions "Deploy platform" (main, 수동 실행)
argocd app wait iris-platform --sync --health --timeout 900   # 확인용
```

준비 SA·ConfigMap·NetworkPolicy는 wave -2, migration Sync hook은 wave -1, Deployment는 wave 0입니다. 성공한 migration Job은 삭제되며 실패한 Job은 남깁니다. 다음 sync(재시도 또는 다음 digest 커밋)는 실패한 Job을 교체하여 다시 실행합니다. 단독 Helm install은 같은 실행 순서를 제공하지 않습니다.

## 실제 배포 확인

- API·Worker가 실제 WAS digest로 실행되고 ImagePullBackOff/CrashLoop가 없는지 확인합니다. build/deploy Pod Identity credential은 각 역할이어야 하고 API·Job·Agent에는 Worker 역할이 없어야 합니다. 실제 IAM 권한은 mock 검사와 별도로 확인합니다.
- migration hook 성공과 RDS Alembic version을 확인합니다. 비밀번호·URL·토큰은 로그에서 제외합니다.
- API `/healthz` 성공과 DB 연결을 확인하는 `/readyz` **204**, ALB target Healthy와 HTTPS hostname/인증서를 확인합니다. 연결 source subnet, API 8000 SG와 RDS 5432 SG/NetworkPolicy를 함께 점검합니다.
- Argo Service 443의 실제 target은 server Pod 8080입니다. Deploy Worker URL 인증서 SAN과 완전한 CA bundle을 확인합니다. TLS 검증을 끄지 않습니다.
- 플랫폼 API/Worker의 지원된 배포 경로로 사용자 서비스를 하나 배포해 `services/{id}/prod/values.yaml`, release trailer, `svc-{id}` Application/workload와 결과 상태를 확인합니다. 사람은 운영 GitOps `services/`를 직접 수정하지 않습니다.
- Error Agent를 켰다면 내부 Service `/healthz`와 정상 인증·LLM 호출을 확인합니다. 공개 Ingress는 생성하지 않습니다. WAS↔Agent 연동은 애플리케이션 구현 범위입니다.

현재 서비스 삭제 경로는 구현되지 않았습니다. ApplicationSet `create-update`는 디렉터리 제거만으로 Application을 지우지 않도록 유지합니다. ApplicationSet 자체 삭제/철거는 별도 절차이며 이 보호와 같지 않습니다.

## RDS 접근과 비밀번호

RDS(`iris-dev-platform`, foundation `database.tf`)는 management 노드와 SSM bridge에서만 5432로 접근합니다. 접속 정보는 Secrets Manager `iris-dev-platform-db`(username·password·host·port·dbname)에 있고 비밀번호는 Terraform state에 없습니다. 플랫폼은 이 master 계정을 `iris-platform-db` Secret 하나로 사용합니다.

```bash
F=terraform/environments/aws/dev/foundation
aws ssm start-session --target "$(terraform -chdir=$F output -raw ssm_bridge_instance_id)" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "host=$(terraform -chdir=$F output -raw platform_db_endpoint),portNumber=5432,localPortNumber=15432"
psql "host=127.0.0.1 port=15432 dbname=iris user=iris sslmode=require"   # 다른 터미널
```

자동 교체는 없습니다. master 비밀번호는 foundation 변수 `platform_db_password_version`을 올려 apply하면 RDS와 Secrets Manager가 함께 바뀝니다.

## 다른 레포의 플랫폼 서비스 추가

레포마다 자기 digest 파일(`platform/aws-dev-management/<repo>.yaml`)만 씁니다.

1. iris-infra PR: Chart에 컴포넌트(고정 필드·템플릿·schema)를 추가하고 `clusters/aws-dev-management/values/platform.yaml`에 실행 방식을, `terraform/config/platform-ecr-repositories.json`에 레포를 둡니다(ECR과 Argo valueFiles 목록의 원본). merge 후 bootstrap.
2. account: `github_ecr_publishers.<repo>`에 그 레포 OIDC subject로 ECR publisher 역할을 추가합니다(관리자 apply).
3. 그 레포에 iris-was의 `.github/workflows/deploy-platform.yml`·`scripts/build-push-ecr.sh`를 복사해 ECR 저장소·역할·`VALUES_FILE`·컴포넌트 키만 바꾸고 secret `GITOPS_APP_PRIVATE_KEY`를 넣습니다.

## 실패·롤백·Secret/CA 회전

migration 실패 시 Job 상태·비밀값 없는 로그를 확인하고 DB 접속·CA·권한을 수정한 뒤 전체 sync를 재시도합니다. Chart는 DB downgrade를 자동 실행하지 않습니다. 배포 전에 RDS 백업과 스키마 호환성을 확인합니다. 이미지 되돌리기는 `iris-gitops-environments`의 해당 digest 커밋을 revert하는 커밋입니다. 실행 방식 values를 되돌릴 때만 root Application을 이전 검토 SHA로 bootstrap합니다. 현재 schema와 이전 코드가 호환되는지 먼저 확인해야 하며, 이전 migration도 `upgrade head`를 실행한다는 점을 고려합니다. 이전 이미지에 현재 DB revision 파일이 없으면 migration이 실패하므로 백업 복구 또는 호환 release 계획 없이 이전 SHA를 sync하지 않습니다. Secret·CA는 Git revision에 포함되지 않아 별도 복구/회전이 필요합니다.

Secret 환경변수는 Pod 생성 시 읽으므로 교체 후 해당 Deployment를 재시작합니다. CA ConfigMap volume은 갱신되지만 이미 초기화된 client/DB pool이 새 CA를 읽지 않을 수 있으므로 CA 회전 뒤에도 관련 Pod를 재시작합니다. 중간에 실패한 migration은 원인 해결 후 sync를 재시도합니다. `platform.enabled=false`는 기존 Application/워크로드의 안전한 철거 명령이 아니며 별도 삭제 승인을 대신하지 않습니다.
