# cluster-baseline

Namespace, 암호화 gp3 StorageClass(WFFC/Delete/expand), application namespace Quota/LimitRange/NetworkPolicy를 선언합니다. 관리 target은 iris-platform, 앱 target은 iris-apps를 사용합니다. Argo와 observability/kube-system은 application 정책의 selector에 포함되지 않습니다. observability는 node exporter를 위해 Pod Security privileged를 사용합니다.

application ingress는 같은 namespace·kube-system·observability, egress는 같은 namespace·CoreDNS53·Pod Identity agent169.254.170.23:80·HTTPS443을 허용합니다. 별도의 ALB 인바운드 허용은 없으며 후속 Ingress 설계와 함께 추가합니다.

`externalAlb.enabled`이면 application namespace에 앵커 Ingress(`{groupName}-anchor`)를 둡니다. 같은 group의 다른 Ingress가 없어도 internet-facing ALB(이름 = group 이름)와 DNS 이름을 유지하고, 인증서·TLS 정책·HTTP→HTTPS를 group 공통으로 정하며, 모르는 host는 404로 응답합니다. Namespace RBAC와 user app ServiceAccount는 후속 플랫폼 범위입니다.

`make helm-check`는 두 target values와 critical manifest를 검사합니다. NetworkPolicy의 실제 enforcement는 `smoke-test --exercise`의 차단 probe로 확인합니다.
