# 연동 계약

deployment·release는 [ADR 0002](../docs/decisions/0002-gitops-deployment.md)로 합의했습니다. target의 EKS infrastructure v1은 현재 운영 스크립트가 사용합니다. 플랫폼 target 등록/백엔드·CLI 연동과 나머지 문서는 **합의 전 초안**입니다.

- [deployment](deployment.md): values schema, release, 이미지 참조
- [target](target.md): 타겟 ID, 인증 참조, 출력 전달
- [GCP target v1](gcp-target.md): 별도 metadata schema·GitOps·ECR·key handoff(제품 연동 전)
- [build](build.md): 고정 SHA, BuildKit Job, 빌드 결과
- [namespace](namespace.md): 프로젝트 등록과 앱 배포 권한
- [release](release.md): 버전과 배포 이력
- [service traffic](service-traffic.md): ALB 로그 기반 Prometheus 지표·라벨·시간 의미 (was/frontend 연동 전 초안)
