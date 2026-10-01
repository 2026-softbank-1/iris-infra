# build

상태: 합의 전 초안.

Job 생성·상태 수집 로직은 백엔드에서 구현합니다. `examples/build-job.yaml`은 현재 비실행 자리표시자입니다.

- 소스 URL과 고정 commit SHA로 빌드합니다. floating branch를 빌드 입력으로 사용하지 않습니다.
- 빌드 플랫폼, push할 ECR image 주소, image digest, 로그 위치와 종료 코드를 기록합니다.
- 관리 클러스터의 BuildKit 전용 ServiceAccount와 Pod Identity 역할을 사용합니다.
- ECR push 권한(CodeBuild)과 Deploy Worker 의 ECR 태그 권한을 구분합니다. Worker 는 chart 를 읽지 않습니다(Argo CD 가 Git tag 로 읽음).
- 빌드 실패 시 이전 성공 배포를 유지합니다.

TODO: ServiceAccount 이름, 자원 상한, deadline, 재시도, rootless/privileged 조건과 cleanup 정책을 확정합니다.
