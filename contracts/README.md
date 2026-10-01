# 연동 계약

deployment·release 는 [ADR 0002](../docs/decisions/0002-gitops-deployment.md)로 합의했습니다. 나머지는 **합의 전 초안**이며 구현 전에 확정합니다.

- [deployment](deployment.md): values schema, release, 이미지 참조
- [target](target.md): 타겟 ID, 인증 참조, 출력 전달
- [build](build.md): 고정 SHA, BuildKit Job, 빌드 결과
- [namespace](namespace.md): 프로젝트 등록과 앱 배포 권한
- [release](release.md): 버전과 배포 이력
