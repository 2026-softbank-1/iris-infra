# iris-onprem-gateway

management 공유 ALB → Nginx → Tailscale HTTP 프록시 → 온프레미스 Traefik 경로를 구성합니다. [활성화·검증·롤백](../../../docs/runbooks/onprem-gateway.md)은 코드 플래그와 기존 Argo main 동기화를 사용합니다.

| 설정 | 동작 |
| --- | --- |
| GitOps `onpremGateway.enabled` | 게이트웨이만 활성화. 현재 true |
| GitOps `services.onprem.enabled` | 기존 온프레미스 앱 배포와 게이트웨이 활성화. 기본 false |
| `publicIngress.enabled` | 차트 기본 true. management values는 초기 false |
| `certificateArn` | 공개 모드에서만 필수. 서울 ACM ARN |
| `host` | `*.internal.likelion.uk` 형태. 한 개 서비스 DNS 라벨만 프록시 |
| `albSourceCidrs` | 공개 모드의 ALB → gateway 허용 CIDR. /0 거부 |
| `upstream` | 고정 VM tailnet FQDN, TCP 80. 요청 Host로 upstream을 선택하지 않음 |

게이트웨이 Pod는 non-root/read-only root filesystem으로 실행하고 `fsGroup: 101`인 64Mi emptyDir에 임시 파일을 씁니다. 이미지 entrypoint 대신 nginx를 직접 실행합니다. HTTP ProxyClass와 `tailscale`의 HTTP 전용 NetworkPolicy를 포함하므로 기존 operator/CRD와 management AppProject 권한이 필요합니다. Tailscale 정책 추가 파일은 기존 정책에 병합하는 조각이며 자동 적용되지 않습니다.

`ci/private-values.yaml`은 인증서 없이 Ingress를 제외합니다. `ci/public-values.yaml`의 ARN은 검증 전용 가짜 값입니다. `make helm-check`는 두 모드, 루트 플래그 조합, 잘못된 Host/CIDR/포트 거부 및 기존 AWS 렌더 보존을 검사합니다. 실제 이미지·NetworkPolicy·ALB/TLS·온프레미스 경로 검증은 적용 후 필요합니다.

`prune: false`이므로 공개 → 비공개 전환 또는 GitOps 플래그 비활성화는 기존 리소스 삭제를 뜻하지 않습니다.
