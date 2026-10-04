# GCP workload 연동 계약 v1

AWS [target v1](target.md)은 그대로 유지합니다. GCP 인프라 metadata는 별도
[schema](gcp-target.schema.json)와 [가상 예시](gcp-target.example.json)로 전달합니다.
Terraform `output -json target` → `make gcp-export` → `.generated/gcp-target.json`
순서이며 state, kubeconfig, Google/AWS token, Sealed Secrets 개인키는 전달하지 않습니다.

타겟 ID는 `gcp-dev-workload`, Kubernetes context는 `iris-gcp-dev-workload`,
Application은 `gcp-svc-{service_id}`, namespace는 `svc-{service_id}`입니다.
관리 Argo CD와 ECR은 AWS에 남지만 서비스 트래픽은
`*.gcp.likelion.uk → GCP global HTTPS Gateway → GKE private Pod` 경로입니다.
AWS SSM·온프레미스 Tailscale 프록시는 이 경로에 포함되지 않습니다.

GCP metadata의 `ecr_service_account_id`(Google SA unique ID)와 `ecr_audience`만
수동 AWS `aws/dev/gcp-access` stack 입력으로 넘깁니다. 두 클라우드가 상대의
Terraform state를 읽지 않습니다. 반환하는 role/repository ARN도 비밀값이 아닙니다.
`dns_authorization` CNAME과 `ip_address` wildcard A 레코드는 DNS 운영자가 적용합니다.

GitOps 디렉터리는 `iris-gitops-environments/services/{service_id}/gcp/values.yaml`입니다.
차트는 새 `iris-service-0.10.0` 태그에 독립적으로 고정합니다. 기존 AWS `0.9.0`,
레거시 on-prem `0.6.0`, 등록 서버 `0.8.0` pin은 각각 유지합니다. GCP의
`route.mode=gateway`는 Deployment로 렌더링하며 `deploymentStrategy`는 생략하거나
`ROLLING`만 지정할 수 있습니다. `CANARY`·`BLUE_GREEN`은 schema가 거부합니다. `workload.kind`는 `app`만 지원하며
GCP의 DB용 storage·권한 설계 전까지 `gateway`와 `database`의 조합도 거부합니다. `route.host`는 base domain 하위 한 label만
허용하며 명령·containerPort·image digest·release·variables는 기존 deployment 계약을
사용합니다. GCP health 경로는 인증 없이 **HTTP 200**을 반환해야 합니다.
AWS ALB의 200–499 matcher는 GCP에 적용되지 않습니다.

차트가 `iris-ecr-pull` dockerconfig Secret과 해당 Secret만 get/patch하는 Role을
생성합니다. GCP 갱신기만 인증 data와 만료 annotation을 갱신합니다. Argo의
ignoreDifferences는 이 두 경로에만 적용하며 RespectIgnoreDifferences를 켭니다.
처음 생성한 Secret은 빈 auths이고 갱신기가 채우므로 최초 Pod는 잠시 pull 대기할
수 있습니다. 환경변수 Secret 이름으로 `iris-ecr-pull`은 예약되어 사용할 수 없습니다.
variables는 **GCP 자체 Sealed Secrets 공개키**로 해당 namespace/name에 다시 봉인합니다.

Deploy Worker는 GCP 배포 시 이 디렉터리와 targetName `gcp`를 선택하고 해당
GCP Application/namespace/공개키를 사용해야 합니다. 배포 삭제는 디렉터리 제거,
rollback은 이전 digest와 그 release의 변수 values 복원입니다. 현재 저장소에는
iris-was가 없어 **제품의 타겟 선택·등록 기능은 구현하지 않았습니다**. 이번 구현은
인프라와 수동 GitOps 배포·handoff 범위입니다.

Google IAM은 Argo 연결 권한만 해당 클러스터로 제한하고 Kubernetes RBAC는 dev
cluster-admin을 부여합니다. AppProject는 동기화 경계이며 cluster-admin을 축소하지
않습니다. 갱신기는 namespace label을 검사하지만 최종 보안 경계는 namespace별
RoleBinding입니다. 테넌트 앱은 Kubernetes API token과 Google/AWS 자격증명이 없습니다.
