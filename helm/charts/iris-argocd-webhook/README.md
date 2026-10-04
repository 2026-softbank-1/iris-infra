# iris-argocd-webhook

GitHub push 를 받아 Argo CD 가 GitOps 변경을 바로 반영하게 하는 공개 경로입니다. management 공유 ALB(`iris-platform-external`)에 Ingress 두 개를 둡니다. 운영 절차는 [runbook](../../../docs/runbooks/argocd-webhook.md) 입니다.

| Ingress | host | 전달 | 받는 쪽 |
|---|---|---|---|
| `argocd-webhook` | `argocd-webhook.internal.likelion.uk` | `/api/webhook`(Exact)만 → `argocd-server:https` (HTTPS) | Application refresh |
| `argocd-appset-webhook` | `argocd-appset-webhook.internal.likelion.uk` | `/api/webhook`(Exact)만 → `argocd-applicationset-controller:http-webhook`(7000) | ApplicationSet git generator 재생성 |

- 같은 host 의 다른 경로는 ALB 가 404 로 끝냅니다(`actions.not-found`). Argo UI·API 는 공개되지 않습니다.
- `*.internal.likelion.uk` 는 이미 management ALB 를 가리키고 ALB 인증서가 덮으므로 DNS·인증서를 새로 만들지 않습니다. `group.order` 900 으로 온프레미스 게이트웨이의 와일드카드 규칙(1000)보다 먼저 맞습니다.
- 두 endpoint 모두 `argocd-secret` 의 `webhook.github.secret` 으로 GitHub 서명을 확인합니다. 이 값이 비어 있으면 서명 없는 요청도 받으므로 Ingress 를 만들기 전에 넣습니다.
- `ci/webhook-values.yaml` 의 ARN 은 검증용 가짜 값입니다.
