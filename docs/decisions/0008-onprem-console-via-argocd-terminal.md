# 0008: 온프레미스 서비스 콘솔은 Gateway 가 Argo CD 터미널을 중계한다

상태: 제안. main merge 만으로는 클러스터가 바뀌지 않는다. Argo CD 의 `exec.enabled`, 역할 토큰, 서버 ClusterRole 갱신은 운영자가 [Console Gateway runbook](../runbooks/console-gateway.md#온프레미스-셸argo-cd-터미널)의 순서대로 적용해야 하고, 그 전까지 온프레미스 콘솔은 동작하지 않는다(AWS 콘솔은 영향 없음). 앱 쪽 결정은 iris-was ADR 0035, AWS 쪽 경계는 [0006](0006-console-gateway-exec-boundary.md).

## 배경

0006 으로 AWS 타깃의 서비스 콘솔이 운영에 올라갔다. 온프레미스 타깃(공용 `onprem` 과 사용자가 등록한 `onprem-{serverKey}`)은 아직 `CONSOLE_TARGET_NOT_SUPPORTED` 다. Gateway 는 management 클러스터에서 workload EKS 로 직접 exec 하지만, 온프레미스 서버는 사용자 서버의 K3s API 에 tailnet 으로만 닿고 그 접속 정보(서버별 SA 토큰)는 Argo CD 의 cluster Secret 에만 있다. 그리고 Gateway 에는 DB 도 없다.

on-prem 세션이 같은 날 만든 iris-was ADR 0034(런타임 로그를 Argo CD 로 읽음)와 infra #89 로 "Control API 가 Argo CD 를 읽기 전용 role 토큰으로 부른다"는 선례와 배선(argocd-server 8080 egress NetworkPolicy, Deploy Worker 와 같은 CA bundle, 프로젝트 role `iris-log-reader`)이 생겼다.

## 결정

- **온프레미스 셸은 Argo CD 내장 터미널을 Gateway 가 중계한다.** Gateway 가 Argo CD 프로젝트 role 토큰(`iris-console`)으로 `argocd-server` 의 `/terminal` WebSocket 에 붙는다. 서버별 SA·토큰 저장·Gateway 의 DB 접근이 필요 없고, Argo 가 이미 가진 tailnet 경로와 cluster 자격증명을 쓴다. AWS 타깃은 0006 의 직접 exec 를 그대로 둔다.
- **AppProject `iris-svc-project` 에 role `iris-console`** 을 둔다(GitOps `helm/gitops/templates/services.yaml`). 정책은 `applications, get` 과 `exec, create`(대상 `iris-svc-project/*`) 둘뿐이다. 토큰은 운영자가 만들어 Secret `iris-console-argocd-token`(`iris-platform`, 키 `CONSOLE_ARGOCD_TOKEN`)에 넣는다. Git 에 없다.
- **Gateway 배선은 API 의 `argocdLogs` 와 같은 모양이다**(iris-platform chart `consoleGateway.argocdTerminal`): ConfigMap 에 `CONSOLE_ARGOCD_SERVER_URL`(Deploy Worker 와 같은 `https://argocd-server.argocd.svc`)과 `SSL_CERT_FILE`, 같은 CA bundle ConfigMap 마운트, 토큰은 optional `secretKeyRef` 환경변수, NetworkPolicy 로 `argocd-server` 8080 egress 만 더 연다. 토큰이 없으면 AWS 경로는 그대로 뜨고 온프레미스 요청만 실패한다.
- **Argo CD 터미널을 켠다**: `helm/bootstrap/values.yaml` 의 `configs.cm."exec.enabled": "true"`. `exec.shells` 는 정하지 않는다(기본 `bash,sh,powershell,cmd` 순서). 켜는 데 argocd-server 재시작이 필요 없다(아래 근거).
- **온프레미스 서버의 Argo SA 가 exec 할 수 있게 ClusterRole 에 `pods/exec`(get·create)를 더한다.** 공용 `onprem` 서버는 이 저장소의 `clusters/onprem-workload/argocd-service-deployer.yaml`, 사용자가 등록한 서버는 iris-was `app/assets/onprem/install.sh` 가 쓰는 같은 규칙이다(두 곳을 같게 유지한다).

### Argo CD v3.5.3 에서 확인한 것 (소스 기준)

운영 Argo 는 chart `argo-cd` 10.9.6(앱 v3.5.3)이다. 아래는 그 태그의 소스(`server/application/terminal.go`·`websocket.go`·`server.go`, `pkg/apis/application/v1alpha1/types.go`, `util/session`)에서 읽은 것이고 운영에서 켜 보기 전까지 실제 동작으로 확인되지 않았다.

- `GET /terminal` 은 인증 미들웨어를 거치며 **토큰을 `Authorization` 헤더가 아니라 쿠키 `argocd.token` 에서만 읽는다.** 프로젝트 role JWT 도 같은 쿠키로 보내야 한다.
- 쿼리 `pod`·`container`·`appName`·`projectName`·`namespace` 가 필수(`appNamespace`, `shell` 은 선택). 인가는 `applications, get` 과 `exec, create` 두 번이고 객체는 `<project>/<app>` 이다. 프로젝트 role 정책에 `exec`·`logs` 리소스가 허용된다(`rbac.ProjectScoped`).
- **Pod 가 그 Application 의 리소스 트리에 속해야 한다**(`Pod doesn't belong to specified app`, 400). 컨테이너가 Running 이어야 한다. 이 검사가 Gateway 가 보내는 `appName`·`namespace` 밖의 Pod 를 막는 1차 방어다.
- WebSocket 텍스트 메시지는 JSON `{operation, data, rows, cols}` 이다. 클라이언트→Argo 는 `stdin`(`data`)·`resize`(`cols`·`rows`), Argo→클라이언트는 `stdout`(`data`, 권한 거부·재연결 안내도 이 operation 의 텍스트)다. 서버가 5초마다 WebSocket ping 을 보내고, 끝낼 때 EOT(`\u0004`)를 쓴다.
- 셸은 `exec.shells` 순서로 하나씩 시도하고 모두 실패하면 400 `Failed to exec container` 다(셸 없는 이미지).
- 실제 exec 는 Argo 가 cluster Secret 의 자격증명으로 한다. 먼저 WebSocket(GET), 업그레이드가 안 되면 SPDY(POST)로 대체한다. 그래서 대상 클러스터의 Argo SA 에 `pods/exec` 의 `get` 과 `create` 가 모두 있어야 안전하다.
- `exec.enabled` 는 요청마다 설정을 읽어 꺼져 있으면 404 다(재시작 불필요). `exec.shells` 는 서버 시작 때 핸들러에 들어가 바꾸면 argocd-server 재시작이 필요하다. 그래서 값을 정하지 않는다.
- chart 가 `exec.enabled: "true"` 를 보면 management 클러스터의 `argocd-server` ClusterRole·Role 에도 `pods/exec`(create)를 더한다(렌더로 확인). 라이브 `argocd-cm` 만 패치하면 이 RBAC 은 생기지 않고, 다음 bootstrap 때 생긴다. `iris-console` role 은 `iris-svc-project`(대상: workload·on-prem)만 가리켜 management 의 Pod 에는 쓰이지 않는다.

## 검토한 대안

- **서버별 exec 전용 SA + Gateway 직접 접속(Control API 가 암호화 저장).** 권한이 가장 좁다. 그러나 설치 스크립트(SA 를 하나 더)·connect 계약(토큰 한 개 더)·Control API 저장·Gateway 가 DB 없이 그 토큰을 읽을 경로·Gateway 의 tailnet 경로가 모두 새로 필요해 iris-was·iris-cli·infra 가 다 바뀐다. 공용 `onprem` 서버는 따로 손으로 해야 한다. 서비스 콘솔이 on-prem 에서 막힌 이유가 "자격증명이 Argo 에만 있다"인데 그것을 복제하는 셈이라 제외.
- **Control API 가 Argo 터미널을 중계.** 0006 의 원칙(Control API 는 클러스터에 닿지 않는다)을 깬다. ADR 0034 의 읽기 전용 예외와도 성격이 다르다(exec 는 쓰기). 제외.
- **서버 쪽에 에이전트를 두고 reverse tunnel.** 가장 일반적이지만 설치 구성 요소·인증·재연결을 새로 만들어야 하고 이번 범위를 넘는다.

## 영향과 위험

- **권한이 넓은 토큰이 Gateway 에 생긴다.** `iris-console` 토큰은 `iris-svc-project` 의 모든 Application 의 Pod 에 exec 할 수 있다. 정책 객체가 `<project>/<app>` 이라 cluster 로 좁힐 수 없다. Gateway 가 티켓에서 `appName`(`svc-{id}`)·namespace·container(`app`)를 고정하지만, 토큰이 유출되면 그 범위를 벗어난 호출이 가능하다. 완화: 토큰은 Gateway Secret 에만 두고 값을 로그·Git 에 남기지 않으며, argocd-server 로 가는 경로는 Gateway·API·Deploy Worker 의 NetworkPolicy 뿐이다. 만료를 두고(runbook: 90일 이하) 교체 절차를 둔다.
- **AWS workload 에도 exec 가 열린다.** `iris-svc-project` 의 destination 에는 workload EKS 도 있고, Argo 의 workload Access Entry 는 `AmazonEKSClusterAdminPolicy` 다. 따라서 위 토큰으로 AWS 사용자 Pod 에 대한 exec 도 Argo 가 수행할 수 있고, 0006 의 `iris-console` group 에만 걸린 ValidatingAdmissionPolicy 는 이 경로를 막지 않는다(caller 가 다르다). 정상 흐름에서 Gateway 는 티켓의 `cluster` 가 `onprem` 일 때만 Argo 를 쓴다. 이 틈을 막으려면 workload 에 "Argo deploy role 의 exec 거절" admission policy 를 더하거나 on-prem 서비스를 별도 AppProject 로 옮겨야 하는데, 둘 다 이번 범위를 넘어 후속으로 남긴다.
- **서버 SA 의 권한이 늘어난다.** on-prem 서버의 Argo SA 는 이미 클러스터 전체 읽기(Secret 포함)와 `svc-*` 쓰기 권한이 있고, 여기에 임의 Pod 의 exec 가 더해진다. 사용자 자신의 서버이고 Argo 의 Application 범위 검사가 1차 방어라는 점에서 받아들인다. 사용자 서버의 다른 namespace Pod 에는 Argo 가 거절한다(Application 트리 밖).
- **Argo 의 `/terminal` 은 UI 내부 엔드포인트다.** 인증(쿠키)과 메시지 형식이 Argo 버전에 묶인다. `helm/versions.json` 의 argo-cd 를 올릴 때 이 경로를 다시 확인한다.
- **기능을 켜면 Argo 관리자도 터미널을 쓸 수 있다.** 기본 정책에서 `admin` 은 모든 것을 할 수 있다. Argo 는 내부 서비스로만 노출돼 있고 관리자만 접근한다([bootstrap runbook](../runbooks/bootstrap.md)).
- **이미 등록된 서버는 반영 전까지 콘솔이 안 열린다**(재실행하기 전 서버는 `get` 이 이미 있어 WebSocket 만으로 될 수도 있으나 보장하지 않는다). 새 서버는 설치 때 받는다.
- Gateway 를 재시작하면 열린 콘솔(AWS·on-prem)이 끊긴다(0006 과 같다).

## 검증 사항

- 정적: `make helm-check`(AppProject role 이 정확히 세 개이고 `exec` 는 `iris-console` 만 갖는다, Gateway 의 optional 토큰·CA 마운트·argocd egress 가 `argocdTerminal` 에만 따라 생기고 꺼지면 없다, 스키마 거절 입력, argocd-cm 의 `exec.enabled=true`·`exec.shells` 없음, 공용 on-prem ClusterRole 의 exec 규칙이 `pods/exec`[get, create] 하나뿐이다).
- 배포 후 확인(미확인): 라이브 `argocd-cm` 패치 직후 `/terminal` 이 404 에서 인증 요청으로 바뀜(재시작 불필요), 프로젝트 role 토큰이 쿠키 `argocd.token` 으로 `/terminal` 에 통과함, 서버 SA 의 `pods/exec` 로 실제 exec 가 됨, 다른 Application 의 Pod 가 거절됨, 셸 없는 이미지가 `Failed to exec container` 로 끝나 Gateway 가 `SHELL_NOT_FOUND` 로 옮김, Gateway→argocd-server 경로의 25초 이상 연결 유지(Argo 의 5초 ping + 화면의 ping).
