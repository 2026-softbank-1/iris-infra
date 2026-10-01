# 플랫폼 이미지 GitHub Actions 템플릿

서울 리전(`ap-northeast-2`)에서 다음 저장소에 이미지를 게시하는 예시입니다.

| 서비스 GitHub 저장소 | `ECR_REPOSITORY` |
| --- | --- |
| `iris-was` | `iris/was` |
| `iris-code-analyzer-agent` | `iris/code-analyzer-agent` |
| `iris-error-check-agent` | `iris/error-check-agent` |

`iris-web` 정적 사이트 배포는 후속 작업입니다. 사용자 앱의 `iris/services/*`와 CodeBuild 캐시는 기존 빌드 경로를 사용합니다.

## Terraform과 최초 적용

저장소 목록의 원본은 `terraform/config/platform-ecr-repositories.json`입니다. Foundation과 account가 같은 파일을 읽고 `${project}/${suffix}` 이름을 계산합니다. ECR 이름에는 환경 접두어가 없습니다. 기존 `environment` 입력은 다른 자원 이름과 태그에 계속 사용됩니다.

Account와 foundation의 **AWS 계정·리전·project가 일치**해야 합니다. 현재 기본값은 project=`iris`, region=`ap-northeast-2`입니다. 서비스 역할 이름을 계산하는 account의 environment 기본값은 `dev`입니다. 인프라 CI도 같은 입력값을 사용해야 합니다.

1. 관리자가 기존 ECR 이름과 registry의 BASIC/ENHANCED 스캔 설정을 확인합니다. 기존 자원이 있으면 import 여부를 검토합니다. 이 코드는 registry 전체 스캔 설정을 변경하지 않으며, ENHANCED에서는 새 저장소가 실제 스캔 필터에 포함되는지 확인해야 합니다.
2. 관리자 인증으로 account의 plan을 검토하고 플랫폼 ECR 관리 정책을 적용합니다. CI가 자기 인증 역할을 변경하지 않습니다.
3. Foundation plan에서 기존 네트워크·CodeBuild·S3의 삭제·교체가 없는지 확인합니다. **Main 반영 또는 main workflow 실행은 bootstrap과 foundation 전체를 자동 apply**하므로 네트워크 작업과 적용 순서를 맞춥니다.
4. ECR 생성 후 foundation의 `platform_ecr_repository_urls`, `platform_ecr_repository_arns` 출력으로 이름을 확인합니다.
5. 아래 publisher 설정을 관리자가 account에 적용하고 역할 ARN을 서비스 GitHub 변수에 설정합니다.

서로 다른 root의 state를 연결하거나 서비스 빌드에 Terraform state 읽기 권한을 줄 필요는 없습니다.

## 서비스별 OIDC 역할

실제 subject prefix는 각 GitHub 저장소에서 조회합니다. Infra 저장소의 prefix를 복사하지 않습니다.

```bash
gh api repos/OWNER/iris-was/actions/oidc/customization/sub
```

반환된 `sub_claim_prefix`를 사용합니다. 기존 형식(`repo:OWNER/REPO`)과 immutable ID 형식(`repo:OWNER@ID/REPO@ID`)을 지원합니다. 조직에서 커스텀 subject를 설정했다면 이 템플릿의 main-ref trust와 일치하는지 먼저 확인합니다.

Account의 로컬 `terraform.tfvars`에 확인한 값으로 다음 입력을 추가합니다. 기본 `github_ecr_publishers = {}`는 역할을 생성하지 않습니다.

```hcl
github_ecr_publishers = {
  was = {
    oidc_subject_prefix = "repo:OWNER/iris-was" # 실제 조회값으로 교체
    repository_names   = ["iris/was"]
  }
  code-analyzer-agent = {
    oidc_subject_prefix = "repo:OWNER/iris-code-analyzer-agent"
    repository_names   = ["iris/code-analyzer-agent"]
  }
  error-check-agent = {
    oidc_subject_prefix = "repo:OWNER/iris-error-check-agent"
    repository_names   = ["iris/error-check-agent"]
  }
}
```

관리자가 account plan/apply 후 `github_ecr_publisher_role_arns` 출력을 확인합니다. 각 역할은 main 브랜치와 STS audience만 신뢰하고 지정 플랫폼 저장소에만 push할 수 있습니다. GitHub Environment를 publish job에 추가하면 OIDC subject가 바뀌므로 trust도 별도로 검토해야 합니다.

## 템플릿 복사와 GitHub 변수

서비스 저장소에 두 파일을 복사합니다.

- `build-push-ecr.yml` → `.github/workflows/build-push-ecr.yml`
- `build-push-ecr.sh` → `scripts/build-push-ecr.sh`

Settings → Secrets and variables → Actions → Variables에 다음 값을 설정합니다.

| 변수 | 값 |
| --- | --- |
| `AWS_ACCOUNT_ID` | 대상 AWS 계정의 12자리 ID |
| `AWS_REGION` | `ap-northeast-2` (기본값) |
| `ECR_PUSH_ROLE_ARN` | 해당 서비스의 publisher 역할 ARN |
| `ECR_REPOSITORY` | 위 매핑의 ECR 이름 |
| `DOCKERFILE` | checkout 루트 기준 Dockerfile 경로, 기본 `Dockerfile` |
| `DOCKER_BUILD_CONTEXT` | checkout 루트 기준 빌드 디렉터리, 기본 `.` |

이미 존재하는 서비스 Dockerfile을 사용합니다. AWS access key·session token을 GitHub에 등록하지 않습니다. 템플릿은 단일 `linux/amd64` 이미지를 빌드합니다.

## 실행과 결과

PR은 AWS 인증 없이 로컬 Docker 이미지만 빌드합니다. Main push 또는 main에서의 수동 실행은 OIDC 인증, ECR 로그인, 빌드·push를 수행합니다. 다른 브랜치의 수동 실행은 게시하지 않습니다.

이미지 태그는 `sha-<전체 commit SHA>-<run ID>-<attempt>`입니다. 같은 commit의 재실행도 새 태그를 사용합니다. `latest`나 registry cache 태그를 갱신하지 않습니다.

Publish job의 `image_digest`, `image_ref` 출력과 실행 요약에 다음과 같은 배포 참조가 남습니다.

```text
123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/iris/was@sha256:...
```

실제 게시를 승인받은 뒤 해당 digest의 pull 및 스캔 결과를 확인합니다. EKS 배포는 별도 작업입니다. 로컬에서 AWS 설정 없이 빌드만 확인하려면 서비스 저장소에서 실행합니다.

```bash
bash scripts/build-push-ecr.sh build
```

## 검증과 보존

이 인프라 저장소에서는 다음 검사로 inventory와 템플릿을 검증합니다.
`.github/workflows/terraform-check.yml`의 `check` job은 같은 검사와 로컬 scratch 이미지 빌드, Terraform mock 테스트를 함께 수행합니다. 이 job은 AWS 인증 권한이 없으며 모든 검사가 통과해야 main의 `deploy` job이 진행됩니다. 서비스 저장소에서 사용할 위 빌드·push workflow는 별도로 복사합니다.

```bash
python3 scripts/check-platform-ecr.py
python3 scripts/tests/test-platform-ecr.py
python3 scripts/tests/test-build-push-ecr.py
bash -n examples/github-actions/build-push-ecr.sh
shellcheck examples/github-actions/build-push-ecr.sh
actionlint .github/workflows/terraform-check.yml examples/github-actions/build-push-ecr.yml
```

Terraform mock 테스트와 fake 명령 테스트는 AWS를 변경하지 않습니다. 실제 Docker fixture 빌드에는 Docker daemon이 필요하며, 실제 AWS 인증과 push/pull은 별도 운영 검증입니다.

```bash
IRIS_ECR_DOCKER_TEST=1 python3 scripts/tests/test-build-push-ecr.py BuildPushTest.test_real_docker_fixture_build
```

Tagged 이미지는 자동 삭제하지 않고 untagged 이미지만 push 후 30일이 지나면 만료합니다. 실행 중인 이미지와 롤백용 이미지의 태그를 보존합니다. ECR 저장·전송 비용이 발생합니다. 게시를 중단하려면 해당 publisher 역할의 권한을 회수하고 workflow를 중단합니다. 저장소 목록 삭제나 코드 되돌리기로 이미지를 정리하지 않습니다.

참고: [GitHub OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws), [ECR 로그인 액션](https://github.com/aws-actions/amazon-ecr-login), [Buildx metadata](https://docs.docker.com/reference/cli/docker/buildx/build/#metadata-file).
