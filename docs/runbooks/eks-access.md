# Private EKS API 접근과 기본 스택 배포

두 EKS의 public API는 꺼져 있습니다. 운영자는 **로컬 AWS IAM 인증 → SSM 관리 EC2 → private EKS API:443**으로 접근합니다. 관리 EC2에는 공인 IP·SSH·인바운드 규칙·Kubernetes 관리자 권한이 없습니다. Argo CD는 관리 노드의 source SG로 앱 API target SG에 접근하고 Pod Identity → deploy role → Access Entry로 인증합니다.

## 배포 전에 필요한 입력

- 카드 없는 Free 계정에서 **EKS 생성과 서비스 quota가 허용되는지** 확인합니다. EC2의 Free eligible 표시는 EKS 사용 허용이나 무료 운영을 뜻하지 않습니다. 제한되면 배포를 멈추며 Paid 전환을 자동으로 하지 않습니다. 크레딧 잔액·만료와 4개 m7i-flex.large의 On-Demand vCPU quota·두 AZ 용량도 확인합니다.
- 운영자 **IAM role/user ARN**을 확정하여 로컬 `operator_principal_arn`과 GitHub 변수 `EKS_OPERATOR_PRINCIPAL_ARN`에 같은 값을 넣습니다. 운영자가 더 있으면 `additional_operator_principal_arns`와 GitHub 변수 `EKS_ADDITIONAL_OPERATOR_PRINCIPAL_ARNS`(JSON 배열)에 넣습니다. STS 세션 ARN과 root ARN은 사용하지 않습니다. 이 principal에는 두 EKS의 cluster-admin Access Entry가 생깁니다.
- 계정·리전·state 버킷·기존 foundation state 위치를 확인합니다. account의 runtime IAM/compute/bridge/EKS 정책과 CI 역할 `max_session_duration=7200`을 **관리자가 main merge 전에** 적용합니다. CI는 account를 적용하지 않습니다.
- private 저장소에는 이 저장소만 읽을 수 있는 GitHub token 또는 read-only SSH deploy key가 필요합니다. 파일로 로컬에 준비하며 Git·환경 값·명령 인수·Terraform state에 키 본문을 넣지 않습니다.1 SSH 방식은 chart의 known_hosts에 GitHub 공식 호스트 키가 있는지 확인하고 불일치하면 중단합니다.
- merge된 검토 완료 commit SHA와 일요일의 실제 철거 시각을 확정합니다. 자동 만료·철거 예약은 없습니다. main merge는 Terraform 인프라 배포의 시작입니다.

Free 플랜은 허용 서비스가 제한되고 크레딧/플랜 만료로 계정 접근이 중단될 수 있습니다. [AWS Free Tier 플랜](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)을 확인합니다. 구현/정적 테스트로 이 계정의 EKS 생성 가능 여부를 보장할 수 없습니다.

## 직접 준비할 로컬 도구

Terraform **1.16.4**, 기존 lock의 AWS provider **6.67.0**, Helm **3.19.1**, kubectl **1.35**, AWS CLI v2, AWS **Session Manager plugin**, Git, Make, Bash, Python 3가 필요합니다. `make helm-check`는 `PyYAML==6.0.3`도 사용합니다. 도구나 AWS 로그인은 스크립트가 자동 설치하지 않습니다. 기존 kubectl 1.33은 이 검증 경로에 사용하지 않습니다.

운영자 AWS 정책에는 STS identity, EKS DescribeCluster/DescribeAddon, EC2 DescribeInstances/Subnets/SecurityGroups/Volumes, SSM DescribeInstanceInformation, bridge instance와 `AWS-StartPortForwardingSessionToRemoteHost` 문서에 대한 StartSession, 자기 세션 Resume/TerminateSession이 필요합니다. 이 정책은 EKS Access Entry와 별개이며 관리자 정책 또는 기존 운영자 역할에서 준비합니다. [SSM 권한](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-restrict-access-quickstart.html), [원격 포워딩](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html#sessions-remote-port-forwarding)을 참고합니다.

## API 경로 확인과 터널

Terraform backend를 기존 설정으로 초기화한 뒤 실행합니다. 출력과 kubeconfig는 `.generated/`에 0600 권한으로 저장하며 Git에서 제외합니다.

```bash
export AWS_PROFILE=iris-operator
export AWS_ACCOUNT_ID=187069338876
export AWS_REGION=ap-northeast-2
make tf-init STACK=aws/dev/management
make tf-init STACK=aws/dev/workload
make export-targets
make eks-preflight TARGET=aws-dev-management
make eks-preflight TARGET=aws-dev-workload
```

preflight는 실제 caller ARN, cluster ARN/endpoint/CA, VPC·private endpoint·두 subnet/AZ·SG 연결, private bridge·인바운드 없음, SSM Online·Agent 3.1.1374.0 이상을 확인합니다. management 노드의 source SG 연결도 확인합니다. API/RBAC·노드·CNI는 열린 터널 위에서 bootstrap/smoke가 추가 확인합니다. 변경된 클러스터를 재사용한 출력으로 접근하지 않습니다.

터미널 두 개에서 하나씩 열고 계속 유지합니다.

```bash
# 터미널 1: management → localhost:10443
make eks-api-tunnel TARGET=aws-dev-management
# 터미널 2: workload → localhost:11443
make eks-api-tunnel TARGET=aws-dev-workload
```

SSM EC2가 대상 hostname을 VPC DNS로 조회하여 443에 연결합니다. 로컬 kubeconfig는 `server=127.0.0.1`과 **원래 EKS hostname의 tls-server-name + 실제 CA**를 함께 사용합니다. 인증서 검증 우회는 하지 않습니다. 이미 사용 중인 로컬 포트, 다른 caller·계정·endpoint·CA·SG이면 중단합니다.

```bash
kubectl --kubeconfig .generated/kubeconfig-aws-dev-management.json --context iris-dev-management get nodes
kubectl --kubeconfig .generated/kubeconfig-aws-dev-workload.json --context iris-dev-workload get nodes
```

## Argo CD와 기본 스택 설치

Terraform은 EKS·노드·CNI/CoreDNS/kube-proxy/Pod Identity/EBS CSI를 설치합니다. 운영자가 아래 명령으로 관리 EKS의 Argo CD를 Helm 설치하고, Argo가 두 EKS에 baseline·LBC·metrics-server·kube-prometheus-stack을 동기화합니다. 이 명령은 실제 Kubernetes 변경이므로 인프라 적용과 별도로 배포 시 실행합니다.

```bash
# 검토 완료 SHA를 checkout하고 작업 트리가 깨끗해야 합니다.
export GITOPS_REVISION=<main에-merge된-40자리-SHA>
export ARGOCD_GIT_TOKEN_FILE=/안전한/로컬/읽기전용-token
# SSH 방식은 위 변수 대신 ARGOCD_GIT_SSH_KEY_FILE 사용
make bootstrap CLUSTER=aws-dev-management
```

두 API의 TLS/RBAC, AZ별 Ready 노드 2개, `allocatable.pods=35`, prefix delegation·CNI network policy agent를 모두 확인한 뒤 처음 쓰기를 시작합니다. token/키와 cluster Secret은 메모리/stdin으로만 전달합니다. chart/이미지 고정값은 `helm/versions.json`, `helm/bootstrap/Chart.lock`, `helm/images.lock.json`에 있습니다. root와 8개 addon Application이 Synced/Healthy여야 완료입니다. Git의 mutable main 추적이나 user app 관리는 하지 않습니다. 버전 갱신은 새 검토 SHA로 다시 bootstrap합니다.

Argo CD/Grafana는 ClusterIP입니다. 터널을 유지하며 별도 터미널에서 로컬 포워딩합니다.

```bash
kubectl --kubeconfig .generated/kubeconfig-aws-dev-management.json -n argocd port-forward service/argocd-server 8080:443 --address=127.0.0.1
kubectl --kubeconfig .generated/kubeconfig-aws-dev-management.json -n observability port-forward service/monitoring-grafana 3000:80 --address=127.0.0.1
```

Argo initial admin 및 Grafana admin password는 각 Secret에서 운영자가 로컬로 조회하고 화면 공유·CI 로그에 출력하지 않습니다. `https://localhost:8080`의 Argo 서버 자체 인증서는 EKS API CA와 별개입니다. Argo/Grafana 사용자 서비스 인증서와 외부 도메인은 후속 설정입니다.

## 배포 후 검증

```bash
make smoke-test TARGET=aws-dev-management
make smoke-test TARGET=aws-dev-workload
```

기본 smoke는 읽기 전용입니다. API/TLS/RBAC·두 AZ Ready 노드·maxPods·managed addon ACTIVE·GitOps health·PVC Bound·metrics API·Prometheus의 kubelet/node exporter/KSM/apiserver 수집을 확인합니다. ECR에 이미지가 없어도 기본 스택 bootstrap을 막지 않습니다. digest pull은 별도 입력이 없으면 **미검증**으로 보고합니다.

추가 자원 생성과 비용을 허용할 때만 다음 검사를 선택합니다. 임시 namespace/1Gi gp3/Python Pod, 기존 LBC/Argo SA로 동작하는 AWS CLI 검사 Pod를 만들고 종료 시 삭제합니다. DNS·NAT HTTPS·NetworkPolicy 차단·암호화 gp3 쓰기/읽기·Pod Identity를 검사합니다.

```bash
bash scripts/smoke-test.sh aws-dev-workload --exercise
bash scripts/smoke-test.sh aws-dev-management --exercise
# 실제 존재하는 ECR digest를 넣은 경우에만 pull 검증
bash scripts/smoke-test.sh aws-dev-workload --exercise --image-ref '187069338876.dkr.ecr.ap-northeast-2.amazonaws.com/iris/was@sha256:<64자리-digest>'
# 앱 노드의 eviction을 별도로 선택. 운영자 검사와 계획된 중단 때만 실행.
bash scripts/smoke-test.sh aws-dev-workload --exercise --drain-node <이-클러스터의-노드명>
```

drain은 workload의 지정 노드만 대상으로 하고 stateless 2 replica가 남은 노드에서 Ready인지 확인합니다. PDB 등으로 drain이 거부되면 실패하며 force eviction은 하지 않습니다. finally에서 uncordon·임시 자원 삭제를 시도하며 실패한 정리는 출력된 정확한 대상만 수동 처리합니다. **단일 replica의 AZ 종속 EBS Prometheus/Grafana/Alertmanager는 HA가 아닙니다.** 실제 사용자 앱에는 별도 replica·probe·topologySpread·PDB와 DB/스토리지 설계가 필요합니다.

외부 인바운드는 아직 생성하지 않습니다. 후속 Ingress에서 도메인·ACM 인증서·public ALB HTTPS·ALB→앱 포트 SG와 application NetworkPolicy 허용 범위를 결정합니다. NAT는 인바운드를 받지 않습니다.
