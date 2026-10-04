# GCP dev workload 배포와 검증

코드·mock·렌더 검증과 실제 배포 성공은 별개입니다. 프로젝트가 아직 없으므로
아래 외부 변경은 실행하지 않았습니다. 프로젝트/billing, Terraform apply,
이미지·chart tag 게시, main push/CI 실행, Kubernetes 배포, DNS 변경은 배포
승인 범위에 포함되어야 합니다. 비밀값·state·plan·kubeconfig는 Git에 넣지 않습니다.

## 준비와 비용

프로젝트 ID와 billing을 준비합니다. 서울 `asia-northeast3-a`, base domain
`gcp.likelion.uk`를 사용합니다. 운영자에게 project 조회, Service Usage API enable,
Compute/GKE·IAM custom role/SA·WIF·Certificate Manager·Secret Manager 관리와 GCS
state object/lock 권한이 필요합니다. Service Usage/Resource Manager API를 사용할
수 있어야 합니다. Google Cloud CLI·ADC·gke-gcloud-auth-plugin, kubectl, 저장소
고정 Terraform, Helm 3.19.1, AWS CLI, Docker/buildx, kubeseal, private Git read
credential을 준비합니다. gcloud와 Terraform ADC 둘 다 해당 project 접근을 확인합니다.

비용 견적에는 GKE 관리, e2-standard-2 **1–2대**와 각 30GiB 디스크, NAT 한 개와
처리량, global HTTPS LB 한 개·forwarding rule·처리량, 고정 IP 한 개, Logging/
Monitoring, GCS state·Secret Manager key versions, ECR storage와 AWS→GCP 전송을
포함합니다. 단일 zone은 dev용이며 HA를 보장하지 않습니다. 공유 관리/ECR 장애는
새 배포·pull·재스케줄링에 영향을 줍니다. AWS DB/API를 호출하는 앱은 별도 의존입니다.

```bash
export GCP_PROJECT_ID=실제프로젝트ID
export AWS_ACCOUNT_ID=실제12자리계정
```

## state → GKE → ECR trust

자동 파이프라인의 flag·변수·OIDC·관리자 account 설정은 [GCP pipeline](gcp-pipeline.md)을
따릅니다. 아래는 관리자 수동 실행 경로이며, account 초기 설정은 양쪽 모두 필요합니다.

`terraform/bootstrap/gcp`의 tfvars/backend example을 실제 파일로 복사합니다.
전역 고유 bucket 이름과 project를 입력하고 backend는 **같은 bucket**, prefix
`gcp/bootstrap`을 사용합니다. 처음에는 전용 `.generated/gcp-state-bootstrap`에서
local backend로 bucket을 만든 뒤 **같은 state**를 GCS로 migration합니다.
migration 및 remote state serial 확인 전 staged state/backup을 삭제하지 않습니다.

```bash
make gcp-state-local-init
make gcp-state-local-plan
make gcp-state-local-apply
make gcp-state-migrate
make tf-init STACK=bootstrap/gcp
make tf-plan STACK=bootstrap/gcp
```

다음으로 `terraform/account/gcp`의 tfvars/backend example에 프로젝트, 기존 bucket,
GitHub numeric repository/owner ID를 설정합니다. prefix는 `gcp/account`입니다.
Service Usage/Resource Manager/IAM/IAMCredentials/STS API와 CI WIF는 이 stack이 소유합니다.
기존 state가 있다면 API ownership 이동을 먼저 별도 리뷰해야 합니다.

```bash
make tf-init STACK=account/gcp
make tf-plan STACK=account/gcp
make tf-apply STACK=account/gcp
```

bucket의 versioning/public 차단/삭제 보호를 확인합니다. workload root의 backend는
같은 bucket의 **별도 prefix** `gcp/dev/workload`입니다. tfvars에는 project와 실제
management EKS 공개 OIDC issuer를 입력합니다. 기본 subnet `10.60.0.0/20`,
pods `10.64.0.0/16`, services `172.22.0.0/20`의 외부 환경과 중복 여부를 확인합니다.
plan에서 독립 VPC/private nodes/DNS API/NAT/1–2 노드/삭제 보호를 검토합니다.

```bash
make tf-init STACK=gcp/dev/workload
make tf-plan STACK=gcp/dev/workload
make tf-apply STACK=gcp/dev/workload
make gcp-export gcp-preflight gcp-access
```

`aws/dev/gcp-access`는 기존 S3 bucket의 별도
`aws/dev/gcp-access/terraform.tfstate` key로 init합니다. GCP export의 Google SA
unique ID와 audience만 tfvars로 전달합니다. 관리자 AWS principal로 role trust의
aud/oaud/sub와 read-only repository 범위를 검토하고 적용합니다. 기존 CI apply
role은 사용하지 않으며 상대 cloud의 remote state를 읽지 않습니다.

```bash
make tf-init STACK=aws/dev/gcp-access
make tf-plan STACK=aws/dev/gcp-access
make tf-apply STACK=aws/dev/gcp-access
```

Google ID token의 azp가 있으면 AWS aud condition은 azp, oaud는 실제 aud를
검사합니다. SA unique ID가 바뀌면 trust도 재검토합니다. 장기 SA/AWS key는 없습니다.

## helper 게시와 최초 pull

게시 승인 후 `iris/gcp-ecr-credentials`에 linux/amd64 이미지를 게시하고 실제
digest를 사용합니다. 게시자의 push 권한은 runtime pull role과 구분합니다.
검증된 main의 immutable SHA 게시와 재사용은 [helper workflow](gcp-pipeline.md)를
사용할 수 있습니다. 다음 직접 게시 명령은 관리자 수동 대안입니다.

```bash
export GCP_ECR_REPOSITORY="$AWS_ACCOUNT_ID.dkr.ecr.ap-northeast-2.amazonaws.com/iris/gcp-ecr-credentials"
export GCP_IMAGE_TAG=검토한소스SHA
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.ap-northeast-2.amazonaws.com"
docker buildx build --platform linux/amd64 --push -t "$GCP_ECR_REPOSITORY:$GCP_IMAGE_TAG" runtime/gcp-ecr-credentials
export GCP_ECR_DIGEST="$(aws ecr describe-images --region ap-northeast-2 --repository-name iris/gcp-ecr-credentials --image-ids imageTag="$GCP_IMAGE_TAG" --query 'imageDetails[0].imageDigest' --output text)"
export GCP_ECR_IMAGE="$GCP_ECR_REPOSITORY@$GCP_ECR_DIGEST"
make gcp-bootstrap
```

bootstrap은 project/network/DNS endpoint와 AWS caller account·게시 digest를 검사합니다.
GCP에 Argo Google SA의 dev cluster-admin binding, `iris-system` namespace와 유효기간
1시간 이상인 초기 `iris-ecr-pull`을 만듭니다. API IP는 `kubernetes` EndpointSlice에서
구합니다. Dataplane V2의 ipBlock에는 Service clusterIP를 사용하지 않습니다.
`.generated/gcp-gitops.json`은 token 없는 metadata이고 0600입니다. token은
subprocess stdin/메모리에만 존재합니다.

helper도 최초 private image pull에 이 Secret이 필요합니다. 이후 Google metadata
→ STS → ECR로 자신과 tenant Secret을 갱신합니다. digest/초기 Secret이 없으면
활성화를 중단합니다. 갱신 실패 시 유효 cache를 유지하지만 만료 후 신규 pull은 실패합니다.

## Argo, DNS와 sealing key

새 chart를 merge한 뒤 검토한 `iris-service-0.10.0` 태그를 게시해 SHA를 확인합니다.
기존 AWS `0.9.0`, 레거시 on-prem `0.6.0`, 등록 서버 `0.8.0` pin은 각각 유지하며
기존 태그는 이동하지 않습니다. addon source는 검토하여 main에 병합한 SHA 또는
main입니다. **공유 scripts/common.sh 변경의 main push는 기존 AWS CI apply를
유발할 수 있습니다.** GCP 경로는 기존 AWS auto-apply에서 제외하며, GCP 자체 workflow는
flag=true 이후 workload runtime 변경을 자동 적용합니다. bootstrap/account/gcp-access는 수동입니다.

두 AWS private API 터널과 기존 운영 credential을 준비하고 opt-in으로 bootstrap합니다.
기존 AWS addon도 확인·조정하므로 그 범위가 배포 승인에 포함되어야 합니다.
clean reviewed checkout과 private Git read credential이 필요합니다.

**Argo 활성화 전에** 권한 있는 DNS 제공자에 export의 `dns_authorization` CNAME과
`*.gcp.likelion.uk A ip_address`를 적용하고 Certificate Manager의 cert ACTIVE를
확인합니다. bootstrap은 cert가 ACTIVE가 아니면 변경 전에 중단합니다. DNS를 나중에
적용하면 Gateway TLS 준비와 addon health 대기가 서로 기다릴 수 있습니다.

```bash
export GITOPS_GCP_ENABLED=1
export GITOPS_REVISION=검토한main에병합된SHA
make bootstrap CLUSTER=aws-dev-management
```

GCP preflight/초기 Secret을 모든 mutation 전에 확인합니다. controller/server에
EKS projected token과 Google ADC ConfigMap을 mount하고 `argocd-k8s-auth gcp`로
연결합니다. DNS endpoint는 공인 CA를 사용하므로 AWS CA를 복사하거나 TLS 검증을
끄지 않습니다. 이미 등록된 GCP가 있는데 opt-in/설정 없는 재bootstrap은 중단합니다.

Route53가 DNS를 관리해도 요청은 GCP에 직접 도착합니다. Gateway Programmed, HTTPS hostname/신뢰 chain을
확인합니다. apex `gcp.likelion.uk`는 wildcard cert/route 대상이 아닙니다.

GCP Sealed Secrets controller는 별도 key를 생성합니다. AWS 개인키는 복사하지
않습니다. 다음은 key를 GCP Secret Manager version에 백업하는 명시적 변경입니다.

```bash
make gcp-backup-keys
```

복구는 project/context를 재검증한 뒤 Secret Manager version payload를 log/stdout/
파일에 노출하지 않고 제한된 운영자 메모리/stdin으로 kube-system에 적용합니다.
controller를 재시작하고 이전 공개키로 봉인한 가상 값을 unseal하는지 확인합니다.
backup/restore 권한은 runtime helper에 부여하지 않습니다. 복구 검증 후 GCP 공개
certificate로 서비스 namespace/name에 맞춰 변수를 봉인합니다.

## 첫 수동 서비스와 실제 완료 조건

iris-was의 제품 target 선택은 이 저장소 범위에 없습니다. 승인된 GitOps repository에
`services/{숫자ID}/gcp/values.yaml`을 수동 추가합니다. 기존 service fixture 형식에
실제 ECR digest/command/containerPort/release/sourceSha, targetName `gcp`,
`route.host=서비스.gcp.likelion.uk`, 인증 없이 **200**인 health.path를 지정합니다.
fixture의 가상 encryptedData는 쓰지 않습니다. AppSet이 Gateway/pull/quota/NP를
주입하므로 AWS ingress 설정을 가져오지 않습니다.

`make gcp-smoke`는 project/node Ready/Gateway Programmed/helper availability만 읽습니다.
배포 승인 후 아래 실제 검증을 수행하며 mock/render로 대신하지 않습니다.

- HTTPS 요청 성공과 HTTP 301, DNS GCP IP, cert/HTTPRoute Accepted/ResolvedRefs,
  backend HEALTHY를 확인합니다. health를 잠시 500으로 바꿔 UNHEALTHY·신규 요청 실패,
  원복 후 HEALTHY·요청 복구를 확인합니다. AWS ALB 200–499 matcher는 GCP에 적용되지 않습니다.
- 가상 환경변수 변경을 관찰하고 이전 digest와 그 release의 변수 values로 rollback합니다.
  GCP 디렉터리 제거 시 `gcp-svc-{id}` Application/finalizer/workload가 정리되고 다른
  AWS/onprem/GCP 서비스가 보존되는지 확인합니다.
- 새 service ID의 빈 auths가 15초 scan 이후 채워지는지 **만료 annotation/상태만**
  확인합니다. 1시간 회전 후 만료시각 연장, 노드에 cache 없는 **별도 ECR digest**의
  실제 pull 성공을 확인합니다. Argo refresh/full sync 후에도 token이 빈 값으로
  되돌아가지 않고 새 Pod가 pull하는지 확인합니다. restart만으로 cache를 배제할 수 없습니다.
- 서로 다른 svc namespace에 승인된 임시 probe Pod를 생성해 same namespace와
  DNS/필요 public dependency 성공, 다른 tenant Pod·node private IP·metadata 실패를
  확인하고 정리합니다. helper의 namespace list/이름 고정 Secret get/patch만 허용,
  Secret list/create/변수 Secret 접근 거부를 `kubectl auth can-i`로 확인합니다.
- AWS DB/API 없는 샘플에서 management의 **GCP 전용 cluster Secret** server를
  임시 거부 DNS endpoint로 바꿔 GCP reconcile만 실패시키고 HTTPS 지속을 확인합니다.
  원래 server로 복구하고 reconcile 복구도 확인합니다. 전체 Argo/AWS 네트워크는
  끊지 않습니다. 이 실험은 신규 ECR pull의 독립성을 주장하지 않습니다.
- GCP Cloud Logging/Monitoring에서 namespace 로그·node/workload 상태와 key
  backup 복구를 확인합니다. helper readiness는 모든 tenant의 pull 성공을 보장하지 않습니다.

## 장애와 복구

Argo 장애는 cluster condition, projected token audience, WIF subject, ADC mount를
확인합니다. zonal IAM resource는 zones 이름, Container API는 locations 별칭을 써
condition은 **그 클러스터의 두 정확한 이름만** 허용합니다. project 전체로 넓히지
않습니다. [공식 IAM 조건](https://docs.cloud.google.com/iam/docs/conditions-resource-attributes),
[GKE DNS 접근](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl),
[Dataplane V2 정책](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/network-policy)을 참고합니다.

token 만료 후 새 helper Pod가 뜨지 못하면 operator의 유효 token으로
`make gcp-bootstrap`을 재실행해 자신의 Secret을 먼저 복구합니다. Google/AWS trust와
public egress, helper readiness/회전/uncached pull을 확인하고 장기 key를 넣지 않습니다.

되돌릴 때 GCP Applications/ApplicationSet 자동 sync를 중단하고 검증한 이전 source/
chart/service values로 돌아갑니다. root prune=false이므로 GCP option만 꺼서 기존
Application이 삭제된다고 가정하지 않습니다. Gateway 삭제는 LB 제거이므로 요청을
이전 경로로 옮긴 뒤 명시적으로 정리합니다. GCS state/sealing key/backup은 보존합니다.
destroy·삭제 보호 해제·state bucket/key/ECR 삭제는 별도 폐기 계획과 승인으로 진행합니다.
