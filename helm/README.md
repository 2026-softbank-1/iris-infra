# Helm과 GitOps

`bootstrap`은 Argo CD 10.9.6 wrapper chart이며 Chart.lock으로 의존성을 고정합니다. `gitops`는 검토한 Git SHA와 실제 두 EKS target을 받아 addon·선택적 플랫폼 AppProject/Application과 사용자 서비스 ApplicationSet을 렌더링합니다. `charts/cluster-baseline`은 Namespace·gp3·Quota·LimitRange·NetworkPolicy를 구현하며 `iris-service`는 사용자 서비스, `iris-platform`은 API·Worker·선택적 Error Agent와 외부 RDS 참조를 구현합니다.

Terraform 소유 managed addon은 Helm으로 중복 설치하지 않습니다. Argo 자체/초기 credential/root는 Helm bootstrap, baseline/LBC/metrics/monitoring과 management의 Loki·OTel gateway는 Argo, 사용자 앱은 Deploy Worker의 GitOps values commit과 별도 Argo ApplicationSet이 소유합니다. 사용자 앱 ApplicationSet은 `iris-gitops-environments/services/*/prod`를 읽습니다. 클러스터별 외부 ALB는 baseline 앵커 Ingress가 유지합니다.

`versions.json`은 Helm/Kubernetes/chart 버전을, `images.lock.json`은 실제 공개 registry manifest의 digest와 opt-in 검증 이미지를 고정합니다. upstream chart의 tag/sha 필드 형식이 다르므로 `make helm-check`로 최종 image 문자열까지 검증합니다. 이 검사는 PyYAML6.0.3과 chart 다운로드가 필요하며 AWS/Kubernetes에 쓰지 않습니다.

GitOps source는 private iris-infra의 immutable SHA만 허용합니다. credentials는 values에 없고 운영자가 bootstrap stdin 경로로 제공합니다. baseline wave0 이후 addon wave1이며 root의 Application health Lua가 하위 health를 반영합니다. root/addon의 automated prune은 꺼져 있으며 철거는 [runbook](../docs/runbooks/teardown.md)에 따릅니다.

[설치와 실제 검증](../docs/runbooks/eks-access.md)을 참고합니다.

`iris-service` 검증은 chart의 `ci/aws-values.yaml`, `ci/local-values.yaml`을 사용합니다. 이전 `examples/*-service-values.yaml`/`service-defaults.yaml`의 초안 필드는 새 계약과 맞지 않습니다. [ADR 0002](../docs/decisions/0002-gitops-deployment.md)와 [deployment 계약](../contracts/deployment.md)을 참고합니다.

`gitops.platform.enabled`는 기본 false입니다. `GITOPS_PLATFORM_ENABLED=1` bootstrap은 운영 values lint/render를 먼저 검사하고 management에 자동 sync `iris-platform` Application을 생성합니다. 컴포넌트별 digest는 `iris-gitops-environments/platform/aws-dev-management/<repo>.yaml`(서비스 레포 workflow가 커밋)에서 읽고, digest가 없는 컴포넌트는 배포하지 않습니다. DB migration Sync hook 이후 workload를 적용합니다. 아직 이미지가 없거나 Secret/CA/hostname/CIDR가 준비되지 않았다면 활성화하지 않습니다. [플랫폼 Chart](charts/iris-platform/README.md), [배포 runbook](../docs/runbooks/deploy-platform.md)을 참고합니다.

플랫폼 Chart와 실행 방식 values는 이 저장소에 유지합니다. 별도 GitOps 저장소는 사용자 서비스 values(`services/`)와 플랫폼 digest(`platform/`)를 담으며 Deploy Worker 쓰기 GitHub App과 Argo 읽기 credential을 분리합니다. 코드 정의와 실제 클러스터 적용 상태는 별도로 확인합니다.
