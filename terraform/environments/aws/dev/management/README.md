# 관리 EKS

상태: scaffold. 실제 AWS 리소스는 아직 선언하지 않았습니다.

구현할 내용: 공통 eks 모듈, 관리 node group, Pod Identity Agent, BuildKit·Worker ServiceAccount 역할 연결. foundation 출력만 참조합니다.

독립 root module이며 state key는 `aws/dev/management/terraform.tfstate`입니다.
기본 입력은 `variables.tf`에 있습니다. 필요한 네트워크·노드·IAM 입력과 출력은 구현 시 추가합니다.

```bash
cd terraform/environments/aws/dev/management # 저장소 루트 기준
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
# 두 파일의 계정·리전·버킷을 실제 값으로 수정
```

저장소 루트에서 `make tf-init STACK=aws/dev/management`, `make tf-plan STACK=aws/dev/management`,
`make tf-apply STACK=aws/dev/management`를 사용합니다. 리소스 구현·검증 후 `.scaffold`를 제거합니다.
현재 파일을 그대로 실행하면 클라우드 구성은 생성되지 않습니다.

bootstrap에서 S3 backend를 준비한 후 backend.hcl을 사용해 init합니다.

실행 순서와 의존 관계는 [bootstrap runbook](../../../../../docs/runbooks/bootstrap.md)을 참고합니다.
provider lock 파일은 첫 init 이후 commit합니다.
