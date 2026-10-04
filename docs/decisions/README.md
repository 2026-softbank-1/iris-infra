# 설계 결정

현재 채택한 방향과 이유를 아래에 기록했습니다. 상세 구현 선택은 새 ADR로 추가합니다.

- [0001: 저장소와 배포 구조](0001-repository-and-deployment.md) (배포 방식은 0002로 대체)
- [0002: GitOps(Argo CD) 배포와 chart 전달](0002-gitops-deployment.md)
- [0003: Private EKS와 addon GitOps](0003-private-eks-and-gitops.md)
- [0004: 사용자 환경변수는 Sealed Secrets 로 앱에 전달](0004-user-variables-sealed-secrets.md)
- [0005: 사용자 서비스 배포 방식은 Argo Rollouts 로 고른다](0005-deployment-strategy-argo-rollouts.md)
- [0006: 서비스 콘솔은 별도 Gateway 가 group 권한으로만 exec 한다](0006-console-gateway-exec-boundary.md)

새 결정은 `NNNN-짧은-이름.md`로 작성하고 상태, 배경, 결정, 영향, 검증 사항을 포함합니다.
