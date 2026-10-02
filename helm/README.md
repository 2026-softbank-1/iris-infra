# Helm과 GitOps

`bootstrap`은 Argo CD 10.9.6 wrapper chart이며 Chart.lock으로 의존성을 고정합니다. `gitops`는 검토한 Git SHA와 실제 두 EKS target을 받아 addon AppProject/Application을 렌더링합니다. `charts/cluster-baseline`은 Namespace·gp3·Quota·LimitRange·NetworkPolicy를 구현하며 `iris-service`는 최신 main에서 구현됐고 `iris-platform`은 scaffold입니다.

Terraform 소유 managed addon은 Helm으로 중복 설치하지 않습니다. Argo 자체/초기 credential/root는 Helm bootstrap, baseline/LBC/metrics/monitoring은 Argo, 사용자 앱은 Deploy Worker의 GitOps values commit과 별도 Argo ApplicationSet이 소유합니다. 이번 bootstrap은 사용자 앱 ApplicationSet을 설치하지 않습니다. 기본 ALB/Ingress는 없습니다.

`versions.json`은 Helm/Kubernetes/chart 버전을, `images.lock.json`은 실제 공개 registry manifest의 digest와 opt-in 검증 이미지를 고정합니다. upstream chart의 tag/sha 필드 형식이 다르므로 `make helm-check`로 최종 image 문자열까지 검증합니다. 이 검사는 PyYAML6.0.3과 chart 다운로드가 필요하며 AWS/Kubernetes에 쓰지 않습니다.

GitOps source는 private iris-infra의 immutable SHA만 허용합니다. credentials는 values에 없고 운영자가 bootstrap stdin 경로로 제공합니다. baseline wave0 이후 addon wave1이며 root의 Application health Lua가 하위 health를 반영합니다. automated prune은 꺼져 있으며 철거는 [runbook](../docs/runbooks/teardown.md)에 따릅니다.

[설치와 실제 검증](../docs/runbooks/eks-access.md)을 참고합니다.

`iris-service` 검증은 chart의 `ci/aws-values.yaml`, `ci/local-values.yaml`을 사용합니다. 이전 `examples/*-service-values.yaml`/`service-defaults.yaml`의 초안 필드는 새 계약과 맞지 않습니다. [ADR 0002](../docs/decisions/0002-gitops-deployment.md)와 [deployment 계약](../contracts/deployment.md)을 참고합니다.
