# cluster-baseline

상태: scaffold. `고정 Namespace, ServiceAccount, RBAC, ResourceQuota, LimitRange와 검증된 NetworkPolicy`를 구현할 위치입니다.
현재 templates에는 manifest가 없으며 install/upgrade해도 앱이나 정책이 배포되지 않습니다.
`values.schema.json`은 객체 형식만 확인하는 임시 schema입니다.

구현 시 values·schema·templates를 함께 추가하고 AWS·로컬 샘플로 lint/render를 검증합니다.
변경 시 `Chart.yaml` version을 올립니다. values 합성 순서는 차트 기본값 → 타겟 기본값 → 배포별 값입니다.
