# AWS 계정 공통 IAM

GitHub OIDC provider와 main 브랜치의 Terraform 자동 apply 역할·정책을 관리합니다.
팀 IAM 그룹·사용자 정책은 아직 구현하지 않았습니다. 기존 IAM 자원은 import하고 관리자 비밀번호는 별도로 관리합니다.
GitHub OIDC provider가 이미 있다면 새로 생성하지 않고 import해야 합니다.

독립 root module이며 state key는 `account/aws/terraform.tfstate`입니다.
기본 입력은 `variables.tf`에 있습니다. GitHub OIDC subject prefix는 API에서 조회한 값을 사용합니다.
역할 ARN은 `terraform_apply_role_arn`으로 출력합니다.

```bash
cd terraform/account/aws # 저장소 루트 기준
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# 두 파일의 계정·리전·버킷을 실제 값으로 수정
```

저장소 루트에서 관리자 AWS 프로필과 AWS_ACCOUNT_ID를 지정하고
`make tf-init STACK=account/aws`, `make tf-plan STACK=account/aws`,
`make tf-apply STACK=account/aws`를 사용합니다. 최초 계획은 OIDC provider·역할·inline policy 2개로 총 4개 생성입니다.
이 stack은 배포 인증의 기반이므로 관리자가 로컬에서 적용하고 CI에서는 직접 apply하지 않습니다.
CI 역할의 권한은 bootstrap 버킷 설정, 배포 root 4개의 state·lock,
현재 foundation의 빌드 입력 S3·CodeBuild·로그 그룹·빌드 역할 2개 관리로 제한됩니다.
CodeBuild에 전달할 수 있는 역할은 빌드용 CodeBuild 역할 하나입니다.
state와 state 버킷 삭제, account state 접근과 CI 자기 역할·OIDC 변경은 허용하지 않습니다.
기존 bootstrap 전용 CI에서는 이 stack의 변경된 정책을 관리자가 먼저 적용한 후 배포 코드를 main에 반영합니다.
후속 VPC·EKS stack을 연결할 때 필요한 권한도 먼저 추가합니다.

bootstrap에서 S3 backend를 준비한 후 backend.hcl을 사용해 init합니다.

실행 순서와 의존 관계는 [bootstrap runbook](../../../docs/runbooks/bootstrap.md)을 참고합니다.
provider lock 파일은 첫 init 이후 commit합니다.

GitHub Actions 변수와 자동 배포 절차는 [Terraform CI runbook](../../../docs/runbooks/terraform-ci.md)을 참고합니다.
