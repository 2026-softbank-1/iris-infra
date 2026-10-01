# release

상태: 합의 전 초안.

차트 원본은 이 저장소에서 관리하고 Chart.yaml version을 올려 OCI 패키지를 배포합니다.
Worker는 지정한 버전, CLI는 같은 버전을 번들하여 사용합니다. latest를 자동 선택하지 않습니다.

배포 기록: source SHA, image reference, chart version, target ID, 비밀값 없는 values snapshot/ref.
AWS는 image digest를 기록하고 로컬 tag 사용 시 이미지 ID도 기록합니다.
현재 성공 버전과 시도 중인 버전은 구분하며 상태·이력은 플랫폼 PostgreSQL에서 갱신합니다.

ECR 저장소는 helm/iris-service. push 목적지는 `oci://REGISTRY/helm`,
설치 목적지는 `oci://REGISTRY/helm/iris-service`와 명시한 버전입니다.

TODO: chart 패키지 전달, 배포 상태 전이, 실패·복구 처리와 이력 보존 규칙을 확정합니다.
