# GCP ECR credential reconciler

GKE metadata의 Google SA ID token을 STS AssumeRoleWithWebIdentity로 교환하고
ECR 12시간 auth를 얻습니다. 1시간마다 회전하고 namespace를 15초마다 확인합니다.
AWS ambient key chain을 사용하지 않으며 API 오류 원문·토큰은 기록하지 않습니다.

label `iris.dev/registry-pull=gcp`와 `iris.dev/target=gcp-dev-workload`인
`svc-{숫자}` namespace 및 자신의 `iris-system`만 처리합니다. 예약된
`iris-ecr-pull`의 label/type을 확인하고 resourceVersion을 포함해 인증 data/expiry만
patch합니다. 새 credential 획득 실패 시 아직 유효한 cache를 유지합니다.

`/readyz`는 자신의 pull Secret 만료까지 5분 이상일 때 성공, `/livez`는 프로세스
생존, `/metrics`는 만료시각과 실패 횟수만 노출합니다. readiness는 모든 tenant의
credential 성공을 보장하지 않으므로 실제 namespace별 pull을 따로 검사합니다.

로컬 fake 검증: `python3 scripts/tests/test-gcp.py`.
hash로 고정한 dependency 설치와 Docker build는 CI에서 검증합니다. Google/AWS IAM
및 실제 ECR pull 의미는 배포 후 별도로 검증해야 합니다.
