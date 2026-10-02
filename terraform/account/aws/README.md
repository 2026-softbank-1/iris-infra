# AWS 계정 공통 IAM

GitHub OIDC provider와 main 브랜치의 Terraform 자동 apply 역할·정책, 선택적인 서비스 이미지 publisher 역할을 관리합니다.
팀 IAM 그룹·사용자 정책은 아직 구현하지 않았습니다. 기존 IAM 자원은 import하고 관리자 비밀번호는 별도로 관리합니다.
GitHub OIDC provider가 이미 있다면 새로 생성하지 않고 import해야 합니다.

독립 root module이며 state key는 `account/aws/terraform.tfstate`입니다.
기본 입력은 `variables.tf`와 `ecr-platform.tf`에 있습니다. GitHub OIDC subject prefix는 API에서 조회한 값을 사용합니다.
역할 ARN은 `terraform_apply_role_arn`으로 출력합니다.

```bash
cd terraform/account/aws # 저장소 루트 기준
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# 두 파일의 계정·리전·버킷을 실제 값으로 수정
```

저장소 루트에서 관리자 AWS 프로필과 AWS_ACCOUNT_ID를 지정하고
`make tf-init STACK=account/aws`, `make tf-plan STACK=account/aws`,
`make tf-apply STACK=account/aws`를 사용합니다. OIDC provider·CI 역할·inline policy 3개에 더해
네트워크 관리형 정책과 기존 역할에 대한 attachment를 관리합니다.
이 stack은 배포 인증의 기반이므로 관리자가 로컬에서 적용하고 CI에서는 직접 apply하지 않습니다.
CI 역할의 권한은 bootstrap 버킷 설정, 배포 root 4개의 state·lock,
foundation 빌드/ECR/네트워크와 새 EKS·runtime IAM·tagged compute·SSM bridge 관리로 구성됩니다.
CodeBuild에 전달할 수 있는 역할은 빌드용 CodeBuild 역할 하나입니다.
state와 state 버킷 삭제, account state 접근과 CI 자기 역할·OIDC 변경은 허용하지 않습니다.
기존 bootstrap 전용 CI에서는 이 stack의 변경된 정책을 관리자가 먼저 적용한 후 배포 코드를 main에 반영합니다.
네트워크 코드의 main 반영 전에 아래 관리형 정책을 먼저 적용합니다. EKS 권한과 session 변경도 main merge 전에 먼저 적용합니다.

## 플랫폼 ECR과 서비스 publisher

`ecr-platform.tf`의 `platform-ecr-resources` inline 정책은 공통 inventory
`terraform/config/platform-ecr-repositories.json`의 플랫폼 저장소 3개만 관리합니다.
기존 state·빌드 정책과 Terraform 주소를 유지하며 이미지 push 권한은 추가하지 않습니다.
ECR 코드의 main 반영 전에 관리자가 이 정책을 적용해야 합니다.

`github_ecr_publishers` 기본값은 `{}`입니다. 실제 서비스 저장소의 OIDC subject prefix와
허용할 ECR 이름을 입력하면 서비스별 역할·push 정책을 생성합니다. 각 역할은 정확한 저장소의
main ref와 STS audience를 요구하며 지정된 플랫폼 ECR만 읽기·push할 수 있습니다.
ECR 인증 token만 지정 리전의 `Resource="*"` 예외를 사용합니다.
publisher에는 Terraform state·IAM 변경·저장소 생성·`iris/services/*` 권한이 없습니다.
역할 ARN은 `github_ecr_publisher_role_arns`로 출력합니다.

입력과 복사용 workflow는 [서비스 빌드 템플릿](../../../examples/github-actions/README.md)을 참고합니다.

## 네트워크 CI 권한

`ci-network.tf`는 `<project>-<environment>-foundation-network` 관리형 정책을 기존 CI 역할에 연결합니다.
기존 state·빌드 inline policy와 주소는 유지합니다. 관리형 정책을 사용해 역할의 inline 정책 합산
크기 한도를 소비하지 않으며 mock 테스트에서 관리형 정책의 6,144자 한도를 검사합니다.

생성 대상은 VPC·subnet·route table·IGW·EIP·zonal NAT·SG·SG rule이며 EC2 작업을 개별 열거합니다.
ARN은 지정 계정·리전·네트워크 리소스 종류로, 생성은 `Project`, `Environment`, `ManagedBy=Terraform`,
`Component=network` request tag로 제한합니다. 기존 부모·변경·삭제 작업은 같은 resource tag를 요구합니다.
태그 생성은 허용된 `ec2:CreateAction`에만 연결하고 이후 소유권 태그 값 변경·삭제는 허용하지 않습니다.
소유권 태그가 누락된 기존 자원을 import할 때는 관리자 확인이 필요합니다.

resource-level 권한을 지원하지 않는 네트워크 `Describe` API만 `Resource="*"`와 지정 리전 조건을 사용합니다.
`DescribeVpcAttribute`는 VPC ARN·소유권 태그로 제한합니다. 이 정책에는 EC2 instance/ENI 생성·변경,
EKS 생성, S3 state, IAM 변경 권한이 없으며 CI는 자신의 역할·관리형 정책을 수정할 수 없습니다.
관련 action/resource/condition 조합은 [AWS EC2 권한 참조](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html)를 기준으로 합니다.

`terraform -chdir=terraform/account/aws test`는 기존 trust/state/build 보호와 새 네트워크 권한·태그 범위를
mock으로 검사하며 실제 AWS IAM 충분성을 보장하지 않습니다. administrator account 적용 → foundation plan 검토 →
별도 승인된 main 반영/배포 순서를 따릅니다. 네트워크 생성과 비용 발생은 foundation 적용 시 시작합니다.

bootstrap에서 S3 backend를 준비한 후 backend.hcl을 사용해 init합니다.

실행 순서와 의존 관계는 [bootstrap runbook](../../../docs/runbooks/bootstrap.md)을 참고합니다.
provider lock 파일은 첫 init 이후 commit합니다.

GitHub Actions 변수와 자동 배포 절차는 [Terraform CI runbook](../../../docs/runbooks/terraform-ci.md)을 참고합니다.

## EKS·SSM·Argo CI 권한

`ci-eks.tf`는 정확한 두 cluster ARN과 addon/nodegroup/access/pod identity child ARN, named runtime IAM role/LBC policy, AWS managed attachment allowlist, service별 PassRole, EKS issuer OIDC와 service-linked-role 생성 권한을 정의합니다. GitHub OIDC나 CI 자기 역할/policy 수정 범위는 없습니다.

`ci-access.tf`는 request/resource owner tags의 SG/LT/compute 변경과 private bridge t3.micro RunInstances를 별도 관리형 정책으로 제공합니다. read-only discovery는 지정 리전에서만 `*`, 새 ENI는 RunInstances 인증의 지역/리소스 예외입니다. AMI는 지정 리전의 Amazon 소유 이미지로 제한하며 IAM의 `ec2:Owner` 조건에는 `amazon` 별칭을 사용합니다. `DescribeImages`의 숫자 `OwnerId`와 이 조건값을 혼동하지 않습니다([AWS 예제](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ExamplePolicies_EC2.html)). IAM 허용 범위는 Amazon 소유 AMI들이며 실제 bridge 선택은 foundation의 고정 AL2023 x86_64 SSM parameter를 유지합니다. instance/volume에는 owner tag가 필요합니다.

AMI 조건 수정은 관리자가 account plan을 검토하고 apply해야 실제 CI 역할에 반영됩니다. foundation workflow는 자신의 IAM 정책을 갱신할 수 없습니다. account 적용 후 수정 코드가 반영된 workflow에서 foundation의 새 plan을 확인하여 재시도합니다. mock 테스트는 AMI·subnet/SG·ENI·instance·volume·생성 태그·PassRole의 제한을 검사하지만 실제 EC2 생성 성공은 배포 후 확인합니다.

네트워크에 더해 EKS/runtime-IAM/runtime-compute/bridge-launch 정책 4개를 붙이며 각각 IAM6144자 한도를 mock으로 검사합니다. CI 역할 `max_session_duration=7200`, workflow STS7200, deploy timeout120분을 함께 적용합니다. `make tf-test`는 실제 로컬 backend/state/tfvars를 사용하지 않습니다. 실제 IAM 충분성은 배포 후 확인합니다.
