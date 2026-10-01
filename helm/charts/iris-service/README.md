# iris-service

상태: scaffold. `사용자 앱 Deployment, Service, Ingress와 선택적인 migration Job`를 구현할 위치입니다.
현재 templates에는 manifest가 없으며 install/upgrade해도 앱이나 정책이 배포되지 않습니다.
`values.schema.json`은 객체 형식만 확인하는 임시 schema입니다.

구현 시 values·schema·templates를 함께 추가하고 AWS·로컬 샘플로 lint/render를 검증합니다.
변경 시 `Chart.yaml` version을 올립니다. values 합성 순서는 차트 기본값 → 타겟 기본값 → 배포별 값입니다.
앱 values 검증 원본은 이 차트의 values.schema.json입니다. repository + digest 또는 tag 중 하나를 사용하도록 schema와 template을 구현합니다. AWS는 digest, 로컬 import는 commit 기반 tag를 사용합니다.
