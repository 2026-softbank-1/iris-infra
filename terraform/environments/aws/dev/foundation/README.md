# 공통 VPC·ECR·IAM

상태: 빌드 자원(`build.tf`), 공유 VPC 네트워크(`network.tf`), 플랫폼 ECR(`ecr-platform.tf`)을 구현했습니다. 코드 구현 상태이며 실제 AWS 적용 여부는 state와 plan으로 확인합니다.

- 구현: 소스 스냅샷 S3(SSE-S3, 1일 만료), CodeBuild `iris-dev-build`(privileged, MEDIUM, 15분), CodeBuild 서비스 역할(`iris/services/*` ECR push), Build Worker 역할(Pod Identity 용).
- `buildspec.yml` 은 iris-was Build Worker 가 넘기는 환경변수와 짝을 이룹니다. 바꿀 때 두 저장소를 함께 봅니다. Railpack CLI 는 install 단계에서 고정 버전·체크섬으로 받습니다.
- 서비스별 ECR 저장소(`iris/services/{service_id}`)는 Build Worker 가 만듭니다.
- 플랫폼 ECR은 `iris/was`, `iris/code-analyzer-agent`, `iris/error-check-agent`입니다. 정적 사이트인 `iris-web`은 별도 후속 배포입니다.
- 네트워크: 공유 VPC, public subnet 2개, 관리용·앱용 private subnet 각 2개, IGW, zonal NAT, routing, 관리→앱 API 접근용 추가 SG 2개.
- EKS·Deployer Worker IAM과 실제 API 접근 연결은 후속 구현입니다.

독립 root module이며 state key는 `aws/dev/foundation/terraform.tfstate`입니다.
기본 입력은 `variables.tf`에 있습니다. 후속 EKS·IAM 연결은 아래 출력 계약을 사용합니다.

```bash
cd terraform/environments/aws/dev/foundation # 저장소 루트 기준
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# 두 파일의 계정·리전·버킷을 실제 값으로 수정
```

저장소 루트에서 `make tf-init STACK=aws/dev/foundation`, `make tf-plan STACK=aws/dev/foundation`,
`make tf-apply STACK=aws/dev/foundation`를 사용합니다.

bootstrap에서 S3 backend를 준비한 후 backend.hcl을 사용해 init합니다.

main CI는 bootstrap 적용 후 이 stack을 자동 적용합니다. CI 역할의 foundation 정책은
관리자가 account stack에서 먼저 적용해야 합니다. 기존 foundation 자원과 state가 있다면
위 S3 key로 state를 이전하거나 import한 후 자동 배포를 연결합니다.
main 반영 시 빌드 자원과 네트워크·플랫폼 ECR이 자동 적용됩니다. 네트워크 코드 반영 전에 account의
`foundation-network` 관리형 정책을 관리자 인증으로 먼저 적용해야 합니다.
CodeBuild 프로젝트 생성 자체는 빌드를 시작하지 않으며 이번 변경에서 CodeBuild를 VPC에 연결하지 않습니다.
EKS 등 후속 자원은 코드·입력·의존성과 CI 권한을 함께 준비합니다.

## 플랫폼 ECR

account와 같은 `terraform/config/platform-ecr-repositories.json`을 읽어 `${project}/${suffix}`로 생성합니다.
account와 foundation의 AWS 계정·리전·project를 맞추고, main 반영 전 account의
`platform-ecr-resources` 정책을 관리자 인증으로 먼저 적용합니다. ECR은 VPC·subnet 출력에 의존하지 않습니다.

저장소는 AES256 암호화, push 스캔, immutable 태그, `force_delete=false`와 `prevent_destroy=true`를 사용합니다.
untagged 이미지만 30일 후 만료하며 tagged 배포·롤백 이미지는 보존합니다. 같은 이름의 기존 저장소가 있다면
생성 전에 import를 검토합니다. 코드를 제거해 이미지를 정리하지 않으며 ECR 저장·전송 비용이 발생합니다.
registry 전체 스캔 설정은 변경하지 않습니다. ENHANCED registry라면 실제 스캔 필터 포함 여부를 확인합니다.

`platform_ecr_repository_urls`와 `platform_ecr_repository_arns`는 suffix를 key로 출력합니다.
다른 서비스 저장소에서 사용할 OIDC·빌드·push 예시는 [서비스 빌드 템플릿](../../../../../examples/github-actions/README.md)을 참고합니다.

## 네트워크 입력과 경로

| 입력 | 기본값 |
| --- | --- |
| `vpc_cidr` | `10.40.0.0/16` (정규 IPv4 network address, /16만 허용) |
| `availability_zones` | `["ap-northeast-2a", "ap-northeast-2c"]` (서로 다른 표준 AZ 2개) |
| `nat_gateway_mode` | `single` (`per_az` 선택 가능) |
| `management_cluster_name` | `iris-dev-management` |
| `workload_cluster_name` | `iris-dev-workload` |

`variables.tf`와 `terraform.tfvars.example`은 같은 기본값을 사용합니다. 현재 CI는 계정·리전만
전달하고 네트워크에는 이 기본값을 사용합니다. example은 자동 로드되지 않습니다.
로컬 값을 바꾸면 main 배포 workflow의 대응 `TF_VAR_vpc_cidr`, `TF_VAR_availability_zones`
(JSON 배열 문자열), `TF_VAR_nat_gateway_mode`, `TF_VAR_management_cluster_name`,
`TF_VAR_workload_cluster_name`도 함께 맞추고 검증하세요. 서울 외 리전은 해당 AZ 2개를 반드시 지정합니다.

| 슬롯 | 기본 AZ | Public | Management private | Workload private |
| --- | --- | --- | --- | --- |
| 0 | `ap-northeast-2a` | `10.40.240.0/24` | `10.40.0.0/20` | `10.40.32.0/20` |
| 1 | `ap-northeast-2c` | `10.40.241.0/24` | `10.40.16.0/20` | `10.40.48.0/20` |

CIDR 슬롯과 Terraform 주소는 AZ 이름의 정렬에 의존하지 않습니다. 다만 목록 순서나 AZ·CIDR 변경은
기존 서브넷 교체를 유발할 수 있으므로 plan을 검토해야 합니다. 배포 전 VPC/VPN/피어링 및 후속
EKS service CIDR과의 중복, 실제 AZ 사용 가능 여부와 IP 여유를 확인합니다.

DNS support/hostnames를 활성화하며 VPC 기본 AmazonProvidedDNS를 사용합니다.
public subnet은 `0.0.0.0/0 → IGW`, private subnet은 `0.0.0.0/0 → NAT` 경로를 가집니다.
모든 subnet의 자동 공인 IPv4 할당은 꺼져 있습니다. public subnet 역할 태그는 ALB discovery에 사용되며,
외부 ALB는 후속 Load Balancer Controller·Ingress가 생성합니다. Terraform이 ALB를 직접 생성하지 않습니다.
외부 서비스 트래픽은 public ALB → private 앱으로 흐르고, NAT는 사설 자원이 시작한 외부 통신과 응답에 사용합니다.

`single`은 슬롯 0의 NAT/EIP 1개를 공유합니다. 해당 AZ 장애 시 두 AZ의 외부 통신이 중단될 수 있으며
슬롯 1에는 AZ 간 전송 비용도 발생할 수 있습니다. `per_az`는 NAT/EIP 각 2개를 만들어 같은 AZ로 라우팅합니다.
실제 적용 시 NAT 시간·처리량, 공인 IPv4, 데이터 전송 비용이 발생합니다. 이번 구성은 IPv4만 지원하며
VPC endpoint, VPN, Flow Logs와 별도 Network ACL 정책은 포함하지 않습니다.

## 후속 stack 출력 계약

| 출력 | 타입·소비자 |
| --- | --- |
| `vpc_id` | string, 두 EKS stack |
| `public_subnet_ids_by_az` | map(string), AZ → ALB용 subnet ID |
| `management_subnet_ids_by_az` | map(string), 관리 EKS control plane·노드 |
| `workload_subnet_ids_by_az` | map(string), 앱 EKS control plane·노드 |
| `management_api_source_security_group_id` | string, 관리 Worker 트래픽의 실제 송신 ENI에 연결 |
| `workload_api_target_security_group_id` | string, 앱 EKS control plane의 추가 SG로 연결 |
| `management_cluster_name`, `workload_cluster_name` | string, EKS 이름과 subnet 태그 일치 |

두 후속 stack은 foundation만 읽고 서로의 state를 참조하지 않습니다. private subnet에는 해당 클러스터의
`kubernetes.io/cluster/<name>=shared`와 `kubernetes.io/role/internal-elb=1`을, 공유 public subnet에는
두 클러스터의 shared 태그와 `kubernetes.io/role/elb=1`을 설정합니다.

source SG의 egress와 target SG의 ingress는 SG 참조 TCP 443만 허용합니다. 이는 사용자 앱의 ALB 인바운드와
별도 경로이며, 이번 단계에서는 SG를 ENI에 연결하거나 EKS endpoint를 만들지 않습니다.
후속 EKS 모듈은 기본 SG·노드 통신·CNI를 구성하고 private endpoint 접근을 활성화해야 합니다.
SG 규칙은 연결된 모든 SG의 합산으로 적용됩니다. 관리 노드에 source SG를 연결하면 해당 노드의 다른
워크로드도 source 범위가 될 수 있으므로 Worker의 실제 송신 ENI와 기존 SG를 포함해 검증합니다.
서브넷 분리만으로 워크로드 격리가 보장되지 않으며 IAM 인증·Access Entry·Namespace RBAC도 별도로 필요합니다.

## 검증과 안전한 적용

`make scaffold-check`, `make tf-check`, foundation/account의 `terraform test`,
`python3 scripts/tests/test-tf-ci.py`로 구조·형식·mock 동작을 검증합니다.
mock 테스트는 실제 AWS API 없이 기본/사용자 지정 토폴로지, NAT 모드, route 연결, SG·태그·출력과
입력 오류를 검사합니다. 실제 IAM 충분성, 이미지 pull, 외부 인바운드와 관리→앱 API 통신은 보장하지 않습니다.

구현 승인과 실제 배포 승인은 별개입니다. 별도 승인 후 관리자 account 권한 적용 → foundation plan 검토 →
main 반영 또는 직접 apply 순서로 진행합니다. 기존 state key와 빌드 주소를 유지하고 기존 빌드 자원 삭제·교체가
나오면 적용을 중단합니다. 전체 foundation destroy는 빌드 입력 버킷·역할까지 삭제하므로 네트워크 복구에 사용하지 않습니다.
배포 후 복구는 참조 EKS/ALB/ENI가 남아 있는지 확인하고 네트워크 제거 변경의 plan을 검토합니다.

실행 순서와 의존 관계는 [bootstrap runbook](../../../../../docs/runbooks/bootstrap.md)을 참고합니다.
provider lock 파일은 첫 init 이후 commit합니다.
