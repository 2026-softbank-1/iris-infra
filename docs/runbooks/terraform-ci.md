# Terraform CI와 main 자동 apply

이 저장소에서는 **사용자가 main으로 merge하면 인프라 검토와 배포 승인이 끝난 것으로 보고 자동 apply**합니다. `.github/workflows/terraform-check.yml`은 PR의 check가 성공한 뒤 main push에서 동일한 check→deploy를 실행합니다. 별도 EKS 수동 토글은 없으며 agent는 main merge/push를 수행하지 않습니다. workflow_dispatch에서 main을 선택해도 배포되므로 운영자가 같은 배포 판단을 하고 실행합니다.

PR check는 AWS 인증 없이 scaffold, fake CI/ops/ECR 테스트, actionlint/ShellCheck, scratch Docker build, backend 없는 fmt/init/validate, 격리 Terraform mock, 고정 Helm chart lint/render/schema/digest 검사를 수행합니다. check timeout은30분이며 하나라도 실패하면 deploy를 시작하지 않습니다. OIDC write는 deploy job에만 있습니다.

| 순서 | root | S3 state key | 적용 내용 |
| --- | --- | --- | --- |
| 1 | bootstrap/aws | bootstrap/aws/terraform.tfstate | 기존 보호된 state 버킷 |
| 2 | aws/dev/foundation | aws/dev/foundation/terraform.tfstate | 기존 build/ECR/VPC + NAT1/route + SSM/Argo IAM |
| 3 | aws/dev/management | aws/dev/management/terraform.tfstate | 관리 EKS·노드·addon·Pod Identity/Access Entry |
| 4 | aws/dev/workload | aws/dev/workload/terraform.tfstate | 앱 EKS·노드·addon·Access Entry |

account는 관리자가 로컬에서 적용하고 module은 독립 apply하지 않습니다. 각 EKS는 foundation만 읽습니다. `.scaffold`가 제거되어 두 EKS가 자동 apply됩니다. CI는 고정 allowlist 순서로 실행하며 새 디렉터리를 자동 검색하지 않습니다.

## main merge 전 준비

1. Free 계정 EKS 서비스 사용 가능 여부, 크레딧/만료, On-Demand vCPU quota와 서울2a/2c 용량을 확인합니다. EKS 불허 시 멈추며 Paid 전환을 자동 수행하지 않습니다.
2. 기존 bootstrap/account/foundation의 **실제 state**가 위 key에 있고 계정·리전·VPC CIDR이 맞는지 확인합니다. 기존 자원을 빈 state로 재생성하지 않습니다. 필요한 state 이전/import는 관리자 계획 검토로 수행합니다.
3. 저장소의 실제 OIDC subject를 조회하여 account trust의 정확한 main prefix/audience와 비교합니다.

```bash
gh api repos/2026-softbank-1/iris-infra/actions/oidc/customization/sub
```

4. 관리자 인증으로 account의 새 EKS/runtime IAM/compute/bridge 정책과 CI role session7200을 **먼저 적용**합니다. 로컬 AWS login은 runner에 전달되지 않으며 runner는 GitHub OIDC만 사용합니다.

```bash
export AWS_PROFILE=iris-tf
export AWS_ACCOUNT_ID=187069338876
make tf-init STACK=account/aws
make tf-plan STACK=account/aws
# 관리자 검토 완료 후
make tf-apply STACK=account/aws
```

account 변경을 적용하기 전에는 [로컬 IAM 검사](../../scripts/README.md)의 account plan 모드로 예정 정책을 검증하고, 적용 후에는 live 모드로 CI 역할의 실제 정책을 확인합니다. simulator/Access Analyzer 결과는 실제 배포 성공과 구분합니다.

5. GitHub Settings → Actions → Variables를 설정합니다.

| 변수 | 값 |
| --- | --- |
| AWS_ACCOUNT_ID | 187069338876 |
| AWS_REGION | ap-northeast-2 |
| TF_STATE_BUCKET | iris-tfstate-187069338876-ap-northeast-2 |
| TERRAFORM_APPLY_ROLE_ARN | account의 terraform_apply_role_arn |
| EKS_OPERATOR_PRINCIPAL_ARN | 운영자 IAM role/user ARN. STS session ARN 제외 |

운영자 ARN은 EKS Access Entry용이며 기존 CI role과 구분합니다. 필수 입력이 없으면 첫 stack 실행 전에 실패합니다. AWS key/session token·Git token·AWS_PROFILE은 GitHub 변수에 넣지 않습니다.

6. foundation 실제 plan에서 기존 build/ECR/VPC/subnet/NAT0/EIP0 보존을 확인합니다. `per_az` 전환은 NAT1/EIP1 추가와 해당 AZ private route 갱신입니다. 실제 plan에는 SSM bridge와 Argo IAM 추가가 있습니다. EKS 최초 plan은 선행 foundation의 새 output이 실제 적용된 이후에 완전히 생성할 수 있습니다. 전체 `tf-ci.sh plan`이 최초에는 후행 output 부족으로 실패할 수 있습니다.
7. 종료 시각과 [철거](teardown.md)를 준비하고 사용자가 merge합니다. main merge는 EKS/NAT 비용 발생을 포함한 인프라 배포의 시작입니다. Helm bootstrap은 merge로 실행되지 않습니다.

## 입력·권한 경계

CI는 `.tfvars.example`을 읽지 않습니다. 기본값은 VPC10.40/16, AZ2a/c, NATper_az, cluster iris-dev-management/workload, projectiris/envdev입니다. 로컬 override를 쓰면 workflow의 대응 `TF_VAR_vpc_cidr`, `TF_VAR_availability_zones`(JSON 배열), `TF_VAR_nat_gateway_mode`, `TF_VAR_management_cluster_name`, `TF_VAR_workload_cluster_name`을 함께 맞추고 account의 cluster ARN scope도 일치시킵니다. 현재 EKS/ops 버전은 승인한 서울2a/c 구성을 전제로 합니다.

기존 state/build/ECR 정책과 주소를 유지합니다. 새 관리형 정책은 named 두 EKS/child ARN·runtime role/policy·attachment allowlist·PassRole service·EKS OIDC issuer, EC2 지역/owner/request tags로 범위를 제한합니다. 런타임 정책은 GitHub OIDC/CI 자신의 IAM을 바꾸지 않습니다. 각 정책6144자 이하·기존 state allowlist·IAM 경계는 mock 검사 대상이며 실제 API 충분성은 별도 확인합니다.

## 순차 apply와 plan 보호

deploy timeout120분, STS role-duration7200초, account maxsession7200초를 함께 사용합니다. 기존 `terraform-apply-bootstrap-aws` concurrency group을 유지하며 새 push가 진행 중 apply를 취소하지 않습니다. S3 use_lockfile/5분 대기와 root별 saved plan을 사용합니다. lock/backend/운영자 입력을 처음에 확인하고 실패하면 뒤 stack을 실행하지 않습니다. 성공한 앞 stack을 자동 rollback하지 않습니다.

foundation은 `terraform show -json <saved-plan>`을 메모리 pipe로 검사하여 build11주소·플랫폼 ECR/lifecycle·VPC·public/private subnet·NAT0/EIP0의 delete/replacement를 차단합니다. moved previous_address도 검사하고 malformed JSON/actions나 show 실패는 apply를 막습니다. 새 NAT1와 route update는 허용합니다. raw JSON·state/plan을 artifact로 올리지 않으며 실패 시 saved plan도 삭제합니다. 이 guard는 모든 위험한 변경을 판별하는 대신 기존 보호 자원에 대한 제한을 제공합니다.

CI 코드 revert는 AWS 자원을 자동 삭제하지 않습니다. 삭제/복구는 실제 state와 별도 철거 plan을 검토합니다. foundation 전체 destroy는 기존 build/ECR을 함께 잃을 수 있어 사용하지 않습니다.

## Provider lock와 검증

Terraform1.16.4와 AWS6.67.0의 기존 lock을 유지합니다. Mac/runner의 darwin_arm64/linux_amd64 체크섬을 등록한 lock으로 `-lockfile=readonly`를 사용합니다. provider를 갱신할 때만 각 root와 module에서 다음 명령으로 두 플랫폼을 검증한 후 diff를 검토합니다.

```bash
terraform -chdir=<root> providers lock -platform=darwin_arm64 -platform=linux_amd64
```

`make tf-check`는 임시 TF_DATA_DIR을 사용하고 `make tf-test`는 backend.hcl·tfvars·state·override를 제외한 임시 복사본에서 mock 테스트합니다. OIDC 실패는 main subject/role/session 설정, S3 실패는 key/tflock, IAM 실패는 해당 runtime action/resource condition부터 확인합니다. 실제 비밀값·state·plan·kubeconfig는 Git에 포함하지 않습니다.

[GitHub OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), [S3 backend](https://developer.hashicorp.com/terraform/language/backend/s3), [EKS IAM](https://docs.aws.amazon.com/service-authorization/latest/reference/list_eks.html), [EC2 IAM](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html), [API/Argo 운영](eks-access.md)을 참고합니다.

## 권한 실패 후 재시도

EC2 실행 권한만 허용해도 provider의 생성 후 조회에서 실패할 수 있습니다. 현재 bridge의 burstable 조회는 `DescribeInstanceCreditSpecifications`, EKS는 `CreateCluster`의 wildcard 예외, EBS addon의 Pod Identity 리소스, Build Worker의 pods 서비스 PassRole, nodegroup/addon 업데이트 조회 범위를 필요로 합니다. [AWS EKS 권한 참조](https://docs.aws.amazon.com/service-authorization/latest/reference/list_eks.html)와 고정 provider 호출 경로를 함께 확인합니다.

1. 관리자 account의 최신 plan을 검토하고 권한 변경을 apply합니다. account는 CI가 갱신하지 않습니다.
2. `python3 scripts/check-eks-ci-permissions.py --role-arn <terraform_apply_role_arn>`을 실행합니다. account 적용 전의 live 거부를 수정 코드의 실패와 혼동하지 않습니다. plan 모드는 관련 ARN/연결이 확정된 account JSON에서 예정 정책만 검사합니다.
3. foundation의 새 plan을 생성하고 기존 build/ECR/network 보호 결과와 bridge의 교체 여부를 확인합니다. 실패 전에 생성된 EC2는 실행 중이고 SSM Online이어도 Terraform state에서 tainted일 수 있습니다. 이런 경우 재시도 plan에 교체가 나타날 수 있고 SSM 접속 대상 ID가 바뀝니다. 운영 상태만으로 자동 untaint/import/destroy하지 않으며 복구는 별도 검토합니다.
4. 검토한 코드가 main에 반영된 뒤 운영자가 승인한 CI 재시도를 진행합니다. 이전 실패의 saved plan은 재사용하지 않습니다.

`CreateCluster` 자체에는 cluster 이름 제한 조건이 없습니다. 리전·소유 태그·private/API/bootstrap/STANDARD 조건을 적용하고, 생성 태그의 `TagResource` 및 이후 관리는 두 named ARN으로 제한합니다. wrong-cluster 검사는 이 ARN 지원 action을 기준으로 합니다. 권한 검사 스크립트는 CI write 권한이나 trust를 확대하지 않고 state·정책·자원을 변경하지 않습니다. 시뮬레이션은 실제 AWS 서비스 권한, CI OIDC, 네트워크, quota, SCP/session/resource policy 등의 최종 결과를 보장하지 않습니다.
