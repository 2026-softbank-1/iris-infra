# iris-onprem-server

사용자가 등록한 온프레미스 서버 하나를 management 의 Argo CD 에 붙입니다. ApplicationSet `iris-onprem-servers`(`helm/gitops/templates/onprem-servers.yaml`)가 GitOps 저장소의 `platform/onprem-servers/{serverKey}/values.yaml` 마다 Application `iris-onprem-server-{serverKey}` 를 만들어 이 chart 를 렌더링합니다. 계약은 iris-was `docs/onprem-server-registration-contract.md` §7·§8, 운영은 [runbook](../../../docs/runbooks/onprem-server-registration.md) 입니다.

| 리소스 | 이름 | 내용 |
|---|---|---|
| SealedSecret | `argocd/cluster-onprem-{serverKey}` | Argo cluster Secret. `name: onprem-{serverKey}`, `server: https://iris-onprem-api-{serverKey}.argocd.svc.cluster.local:6443` 은 chart 가 정하고, 봉인된 `config`(bearer token·CA·serverName)만 data 파일에서 옵니다. management Sealed Secrets controller 가 풉니다 |
| Service(ExternalName) | `argocd/iris-onprem-api-{serverKey}` | Tailscale egress → `{tailnetFqdn}:6443`. ProxyClass `iris-onprem-api`, 태그 `tag:iris-onprem-api` |
| Service(ExternalName) | `onprem-gateway/iris-onprem-apps-{serverKey}` | Tailscale egress → `{tailnetFqdn}:80`. ProxyClass `iris-onprem-http`, 태그 `tag:iris-onprem-apps`. 게이트웨이가 host 의 `-{serverKey}` 로 고릅니다 |
| Application | `argocd/iris-onprem-probe-{serverKey}` | project `iris-onprem-probe`, `destination.name: onprem-{serverKey}`, namespace `iris-system` 에 `iris-onprem-probe` chart(ConfigMap 1개). Synced+Healthy 면 Deploy Worker 가 서버를 `CONNECTED` 로 둡니다 |

- 모든 이름은 `server.key` 에서 나옵니다. 디렉터리 이름(`directoryKey`, ApplicationSet 이 넣음)·`clusterName`·`tailnetFqdn` 이 key 와 맞지 않으면 렌더링에 실패합니다. 포트는 6443·80 만 받습니다.
- ProxyClass·NetworkPolicy 는 서버끼리 공유하므로 `iris-onprem-gateway` chart 에 있습니다.
- probe Application 에는 resources finalizer 가 없습니다. 서버를 지울 때 이미 사라진 클러스터를 기다리지 않게 하려는 것이고, 서버에 ConfigMap 하나가 남습니다.
- 서버 디렉터리를 지우면 ApplicationSet 이 Application 을 지우고 finalizer 가 위 리소스를 지웁니다(SealedSecret → Secret 은 ownerReference 로 함께 지워짐).
- `ci/server-values.yaml` 은 data 파일과 ApplicationSet 값을 합친 모양입니다. 값은 가짜라 실제로 풀리지 않습니다. `make helm-check` 가 렌더·schema 거절·AppProject 허용 범위를 검사합니다.
