# GCP ECR read access — 수동 관리자 stack

기존 AWS state bucket에서 별도 key `aws/dev/gcp-access/terraform.tfstate`를
사용합니다. foundation/account/EKS state는 읽거나 수정하지 않습니다.
Google SA unique ID·audience와 실제 AWS account ID를 명시합니다.

`iris-dev-gcp-ecr-pull` 역할은 Google의 aud(azp)·oaud(aud)·sub를 모두 고정해
AssumeRoleWithWebIdentity만 허용합니다. ECR authentication과
`iris/services/*`, `iris/gcp-ecr-credentials` download만 허용하고 push는 허용하지
않습니다. helper repository는 immutable tag, scan-on-push, 삭제 보호를 사용합니다.
Google는 AWS 내장 federation provider이므로 새 IAM OIDC provider를 만들지 않습니다.

관리자 AWS principal로만 `make tf-init/tf-plan/tf-apply STACK=aws/dev/gcp-access`
명령을 사용합니다. 이 경로는 Terraform CI **자동 apply 대상에서 제외**되어 있고
기존 GitHub apply role에 권한을 추가하지 않습니다. 공유 `scripts/common.sh` 변경을
main에 push하면 기존 AWS CI apply가 실행될 수 있으므로 push는 별도 승인 대상입니다.

이미지 게시와 digest 확인·초기 pull Secret 주입 순서는 [GCP 런북](../../../../../docs/runbooks/gcp-workload.md).

`enable_github_publisher`는 기본 false다. 관리자가 실제 `github_oidc_subject_prefix`를
확인하고 true로 적용하면 한 helper repository의 push/read만 가능한 전용 publisher
role을 만든다. 기존 account/aws의 GitHub provider ARN과 정확한 main-ref subject를
재사용한다. publisher workflow는 environment를 사용하지 않으며 테스트/Docker build가
성공한 main에서 flag=true일 때만 인증한다. pull role의 권한은 바뀌지 않는다.
활성화와 digest handoff는 [GCP pipeline](../../../../../docs/runbooks/gcp-pipeline.md)을 따른다.
