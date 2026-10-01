# Terraform state용 S3 버킷

S3 버킷, versioning, AES256 서버 측 암호화, public access block을 관리합니다.
버킷에는 prevent_destroy를 적용하며 force_destroy는 false로 유지합니다.
prevent_destroy는 해당 리소스 선언이 유지되는 동안 Terraform의 삭제·교체를 막습니다.
버킷명은 `<project>-tfstate-<aws_account_id>-<aws_region>`입니다.

독립 root module이며 state key는 `bootstrap/aws/terraform.tfstate`입니다.
기본 입력은 `variables.tf`에 있습니다. 생성 후 버킷명과 ARN을 출력합니다.

```bash
cd terraform/bootstrap/aws # 저장소 루트 기준
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# terraform.tfvars의 계정·리전을 실제 값으로 수정
# backend.hcl의 버킷명은 위 명명 규칙과 일치시키고 계정·리전을 수정
```

저장소 루트에서 AWS 프로필과 실행 대상 계정을 지정한 뒤 실행합니다.

```bash
export AWS_PROFILE=iris-tf
export AWS_ACCOUNT_ID=<실제_12자리_계정_ID>
make tf-init STACK=bootstrap/aws
make tf-plan STACK=bootstrap/aws
# 최초 계획은 버킷 및 설정 4개 생성이어야 합니다.
make tf-apply STACK=bootstrap/aws
```

최초에는 `backend.tf`의 S3 선언을 주석 상태로 유지하여 로컬 state를 사용합니다.
apply 성공 후 `terraform -chdir=terraform/bootstrap/aws output -raw state_bucket_name`으로
실제 버킷명을 확인하고 `backend.hcl`의 bucket과 일치시키세요.
그런 다음 `backend.tf`의 S3 선언을 활성화합니다.

```hcl
terraform {
  backend "s3" {}
}
```

저장소 루트에서 state를 이전합니다. 버킷 생성 전에 S3 선언을 활성화하면 init이 실패합니다.

```bash
terraform -chdir=terraform/bootstrap/aws init -migrate-state -backend-config=backend.hcl
terraform -chdir=terraform/bootstrap/aws state list
make tf-plan STACK=bootstrap/aws
```

이전 확인 프롬프트에는 `yes`를 입력합니다. 이전 후 4개의 관리 리소스가 표시되고,
plan이 `No changes`이면 정상입니다. 이전에는 `-reconfigure`를 사용하지 않습니다.

실행 순서와 의존 관계는 [bootstrap runbook](../../../docs/runbooks/bootstrap.md)을 참고합니다.
provider lock 파일은 첫 init 이후 commit합니다.
