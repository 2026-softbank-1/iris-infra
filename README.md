# iris-infra

Iris의 AWS 인프라, Kubernetes 공통 설정, Helm/GitOps와 운영 절차를 관리합니다. API/Worker의 배포 오케스트레이션과 서비스 소스는 각 서비스 저장소가 관리합니다.

공유 VPC·빌드 자원·플랫폼 ECR에 더해 **private 관리/앱 EKS, SSM API 접근, Argo CD와 두 클러스터의 관측 스택**을 코드로 구현했습니다. 실제 AWS/Kubernetes 적용 성공은 별도 배포 후 확인해야 합니다. `iris-service` chart는 최신 main에서 구현됐으며 사용자 앱은 GitOps로 배포합니다. `iris-platform`과 로컬 k3d는 계속 scaffold입니다.

| 위치 | 책임 |
| --- | --- |
| `terraform/bootstrap/aws` | 보호된 S3 state 버킷 |
| `terraform/account/aws` | 관리자 선적용 GitHub OIDC/CI IAM |
| `terraform/environments/aws/dev/foundation` | 공유 VPC·AZ별 NAT·SSM bridge·Argo IAM·기존 CodeBuild/ECR |
| `terraform/environments/aws/dev/management` | 관리 EKS·노드·Argo/Build Worker Pod Identity |
| `terraform/environments/aws/dev/workload` | 앱 EKS·노드·운영자/Argo Access Entry |
| `terraform/modules/eks` | EKS 1.35·AZ별 MNG·managed addon·SG·IAM |
| `helm/bootstrap` | 관리 EKS Argo CD Helm 설치 |
| `helm/gitops` | 두 EKS의 baseline/LBC/metrics/monitoring Applications |
| `helm/charts`, `clusters` | baseline·iris-service chart 및 target별 values |
| `scripts`, `contracts`, `docs/runbooks` | 검증·API 접근·연동 계약·운영 |

사용자 앱은 Deploy Worker가 `iris-gitops-environments`에 values를 커밋하고 Argo ApplicationSet이 `iris-service`의 고정 Git tag를 사용해 배포합니다([ADR 0002](docs/decisions/0002-gitops-deployment.md)). bootstrap은 공통 addon과 사용자 앱 ApplicationSet `iris-svc-appset`(AppProject `iris-svc-project`, workload `svc-*` namespace)를 설치합니다. Argo CD는 같은 읽기 전용 자격 증명으로 두 저장소를 읽습니다.

각 클러스터는 서울 2a/2c에 m7i-flex.large On-Demand 노드 1대씩 두며 전체 4대입니다. NAT 기본값은 `per_az`입니다. EKS API public endpoint는 만들지 않습니다. 외부 ALB는 클러스터마다 1개로, baseline의 앵커 Ingress가 유지합니다: management `iris-platform-external`(Control API), workload `iris-service-external`(사용자 서비스, `*.likelion.uk`). Argo/Grafana는 SSM 터널을 거쳐 로컬에서 접근합니다.

## 검증

Terraform **1.16.4**, 기존 AWS provider **6.67.0** lock, Helm **3.19.1**, Python 3 + `PyYAML==6.0.3`을 사용합니다. 운영에는 kubectl **1.35**, AWS CLI v2와 Session Manager plugin도 직접 준비합니다.

```bash
make scaffold-check
make tf-check
make tf-test
python3 scripts/tests/test-tf-ci.py
python3 scripts/tests/test-eks-ops.py
make helm-check
```

mock/정적/렌더 검사는 AWS 배포나 IAM 충분성·실제 네트워크 연결을 증명하지 않습니다. 테스트는 실제 backend/state/tfvars 대신 임시 데이터 디렉터리와 복사본을 사용합니다. provider/chart 다운로드에는 인터넷 접근이 필요합니다.

## 배포

**main merge 후 check → bootstrap → foundation → management → workload가 자동 apply됩니다.** account는 관리자가 먼저 적용하며 CI는 account를 변경하지 않습니다. 계정/기존 state·CIDR·Free EKS 사용 가능 여부·quota를 확인하고 GitHub `EKS_OPERATOR_PRINCIPAL_ARN`과 2시간 CI role session을 준비한 후 사용자가 merge합니다. 기본값 변경은 CI의 대응 `TF_VAR_*`도 맞춰야 하며 `.example`은 CI에서 읽지 않습니다.

인프라가 적용되면 [private API/기본 스택 runbook](docs/runbooks/eks-access.md)의 출력 export·두 SSM 터널·검토 SHA와 private Git credential 준비를 거쳐 `make bootstrap CLUSTER=aws-dev-management`를 실행합니다. ECR 이미지가 없어도 기본 스택은 설치하며 실제 platform digest pull은 입력이 있을 때만 검증합니다.

실제 tfvars/backend/state/plan/Secret/kubeconfig는 Git에 올리지 않습니다. 기존 빌드/ECR/state를 보존하는 [철거 절차](docs/runbooks/teardown.md)를 사용하며 foundation 전체 destroy나 자동 철거는 제공하지 않습니다.

## 문서

- [아키텍처](docs/architecture.md), [설계 결정](docs/decisions/README.md)
- [계정 준비](docs/runbooks/bootstrap.md), [main 자동 apply/CI](docs/runbooks/terraform-ci.md)
- [API 접근·Argo·관측 스택](docs/runbooks/eks-access.md), [후속 플랫폼 배포](docs/runbooks/deploy-platform.md)
- [사용자 환경변수(Sealed Secrets)](docs/runbooks/sealed-secrets.md)
- [사용자 서비스 배포 방식(Argo Rollouts)](docs/runbooks/argo-rollouts.md)
- [문제 확인](docs/runbooks/troubleshooting.md), [철거](docs/runbooks/teardown.md)
- [팀 명령](scripts/README.md), [target 계약](contracts/target.md)
- [서비스 ECR 빌드 템플릿](examples/github-actions/README.md)
