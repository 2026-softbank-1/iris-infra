# release

상태: 합의됨 (2026-10-02, [ADR 0002](../docs/decisions/0002-gitops-deployment.md)).

## chart 전달

chart 원본은 이 저장소의 `helm/charts/iris-service` 입니다. `Chart.yaml` version 을 올리면 같은 이름의 Git tag
`iris-service-<version>` 을 만듭니다. Argo CD ApplicationSet 은 이 tag 를 `targetRevision` 으로 고정해 읽습니다.
latest·브랜치를 자동 선택하지 않습니다. OCI 패키지는 로컬 CLI 가 필요로 할 때 추가합니다.

## 배포 기록

배포 이력·상태는 플랫폼 PostgreSQL(iris-was `releases`)이 원본입니다.
기록: source SHA, image digest, GitOps commit SHA, 이전 정상 release. 비밀값 없는 values 는 GitOps commit 으로 재현합니다.
현재 성공 버전(lastKnownGood)과 시도 중인 버전을 구분하며, 실패 시 GitOps revert commit 으로 되돌립니다.

TODO: chart version 을 release 마다 기록할지(현재는 ApplicationSet 공통 고정), 이력 보존 기간을 정합니다.
