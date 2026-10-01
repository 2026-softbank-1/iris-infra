# 공통 VPC·ECR·IAM

상태: 빌드 자원만 구현했습니다(`build.tf`).

- 구현: 소스 스냅샷 S3(SSE-S3, 1일 만료), CodeBuild `iris-dev-build`(privileged, MEDIUM, 15분), CodeBuild 서비스 역할(`iris/services/*` ECR push), Build Worker 역할(Pod Identity 용).
- `buildspec.yml` 은 iris-was Build Worker 가 넘기는 환경변수와 짝을 이룹니다. 바꿀 때 두 저장소를 함께 봅니다.
- 서비스별 ECR 저장소(`iris/services/{service_id}`)는 Build Worker 가 만듭니다.
- 남은 일: VPC, subnet, routing, 공통 접근용 security group, helm/iris-service OCI 저장소, Deployer Worker IAM 역할, Railpack 커스텀 빌드 이미지.

독립 root module이며 state key는 `aws/dev/foundation/terraform.tfstate`입니다.
기본 입력은 `variables.tf`에 있습니다. 필요한 네트워크·노드·IAM 입력과 출력은 구현 시 추가합니다.

```bash
cd terraform/environments/aws/dev/foundation # 저장소 루트 기준
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# 두 파일의 계정·리전·버킷을 실제 값으로 수정
```

저장소 루트에서 `make tf-init STACK=aws/dev/foundation`, `make tf-plan STACK=aws/dev/foundation`,
`make tf-apply STACK=aws/dev/foundation`를 사용합니다.

bootstrap에서 S3 backend를 준비한 후 backend.hcl을 사용해 init합니다.

실행 순서와 의존 관계는 [bootstrap runbook](../../../../../docs/runbooks/bootstrap.md)을 참고합니다.
provider lock 파일은 첫 init 이후 commit합니다.
