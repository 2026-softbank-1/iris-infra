# iris-onprem-gateway

management 공유 ALB → Nginx → Tailscale HTTP 프록시 → 온프레미스 Traefik 경로를 구성합니다. [활성화·검증·롤백](../../../docs/runbooks/onprem-gateway.md)은 코드 플래그와 기존 Argo main 동기화를 사용합니다.

| 설정 | 동작 |
| --- | --- |
| GitOps `onpremGateway.enabled` | 게이트웨이만 활성화. 현재 true |
| GitOps `services.onprem.enabled` | 기존 온프레미스 앱 배포와 게이트웨이 활성화. 기본 false |
| `publicIngress.enabled` | 차트 기본 true. management values는 초기 false |
| `certificateArn` | 공개 모드에서만 필수. 서울 ACM ARN |
| `host` | `*.internal.likelion.uk` 형태. 한 개 서비스 DNS 라벨만 프록시. 라벨이 `-{serverKey}`(영문으로 시작하는 8자)로 끝나면 그 서버로 보냄 |
| `albSourceCidrs` | 공개 모드의 ALB → gateway 허용 CIDR. /0 거부 |
| `upstream` | 기존 VM tailnet FQDN, TCP 80. `-{serverKey}` 로 끝나지 않는 Host 의 upstream |

사용자가 등록한 서버([온프레미스 서버 등록](../../../docs/runbooks/onprem-server-registration.md))는 Host 의 `-{serverKey}` 8자만 뽑아 `iris-onprem-apps-{serverKey}.<namespace>.svc.cluster.local:80` 으로 보냅니다. 이 egress Service 는 서버마다 `iris-onprem-server` chart 가 만들고, 게이트웨이는 서버를 추가해도 바뀌지 않습니다. 등록되지 않은 key 는 이름을 풀지 못해 502 이며 기존 VM 으로 가지 않습니다. Host 의 나머지는 upstream 이름에 들어가지 않습니다.

이 chart 는 on-prem 공용 Tailscale 설정도 가집니다: 앱 HTTP 프록시용 `iris-onprem-http`, 서버별 Kubernetes API 프록시용 `iris-onprem-server-api` ProxyClass 와 각 NetworkPolicy(HTTP 는 게이트웨이에서 TCP 80, API 는 Argo CD application controller·server 에서 TCP 6443 만). 기존 VM 의 손으로 만든 `iris-onprem-api`(ProxyClass·Service·NetworkPolicy `iris-onprem-api-ingress`)는 이 chart 가 소유하지 않고 바꾸지 않습니다.

게이트웨이 Pod는 non-root/read-only root filesystem으로 실행하고 `fsGroup: 101`인 64Mi emptyDir에 임시 파일을 씁니다. 이미지 entrypoint 대신 nginx를 직접 실행합니다. HTTP ProxyClass와 `tailscale`의 HTTP 전용 NetworkPolicy를 포함하므로 기존 operator/CRD와 management AppProject 권한이 필요합니다. Tailscale 정책 추가 파일은 기존 정책에 병합하는 조각이며 자동 적용되지 않습니다.

`ci/private-values.yaml`은 인증서 없이 Ingress를 제외합니다. `ci/public-values.yaml`의 ARN은 검증 전용 가짜 값입니다. `make helm-check`는 두 모드, 루트 플래그 조합, 잘못된 Host/CIDR/포트 거부 및 기존 AWS 렌더 보존을 검사합니다. `scripts/tests/test-onprem-gateway.py`(Nginx 1.30 필요, CI 밖)는 렌더된 설정으로 기존·서버별 Host 라우팅을 로컬에서 실행합니다. 실제 이미지·NetworkPolicy·ALB/TLS·온프레미스 경로 검증은 적용 후 필요합니다.

`prune: false`이므로 공개 → 비공개 전환 또는 GitOps 플래그 비활성화는 기존 리소스 삭제를 뜻하지 않습니다.
