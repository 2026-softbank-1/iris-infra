# deployment

상태: 합의 전 초안.

앱 values 검증 원본은 `helm/charts/iris-service/values.schema.json`입니다. 현재 schema는 임시입니다.
값은 chart 기본값 → 타겟 기본값 → 배포별 생성값 순서로 합성합니다.

- image.repository와 digest/tag 중 하나. AWS는 digest, 로컬 import는 commit tag.
- containerPort, service.port, envSecretName, health.path, resources, ingress를 합의합니다.
- release 이름·Namespace 이름의 길이·문자·충돌 규칙을 백엔드와 확정합니다.
- 실제 env는 SSM 또는 로컬 설정에서 Secret으로 전달하고 Git에는 참조만 기록합니다.
- schema와 서버 측 허용 필드로 사용자 입력이 권한·타겟 정책을 바꾸지 못하게 제한합니다.

TODO: 필드와 필수값, timeout·rollback·health 검증 규칙을 확정합니다.
