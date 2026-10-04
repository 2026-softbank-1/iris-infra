# 0006: 서비스 콘솔은 별도 Gateway 가 group 권한으로만 exec 한다

상태: 제안. main merge 후 CI 가 새 AWS 리소스를 만들고, root 갱신·키 생성·digest 배포 이후에 동작한다. 실제 클러스터 동작은 배포 후 검증 전까지 확인된 것이 아니다. 앱 쪽 결정은 iris-was ADR 0033, 절차는 [Console Gateway runbook](../runbooks/console-gateway.md).

## 배경

사용자가 서비스 화면에서 실행 중인 Pod 의 `app` 컨테이너에 셸을 열 수 있어야 한다(AWS 타깃 먼저). 지금은 Control API 가 클러스터에 접근할 수 없고(iris-was 의 build-deploy-flow 설계 문서 §7), Prod 클러스터는 Argo CD 만 접근한다. Argo CD 의 내장 터미널(`exec.enabled`)은 꺼져 있다.

## 결정

- **새 컴포넌트 Console Gateway**(management `iris-platform`, iris-was 이미지의 다른 실행 명령)만 workload EKS 에 exec 한다. Control API 는 소유권 확인과 60초 ticket 서명만 하고 클러스터 자격증명을 갖지 않는다. 서명은 Ed25519 비대칭이라 Gateway 는 공개키만 가진다.
- **권한은 Kubernetes 안에만 둔다.** Gateway 의 IAM role 은 권한 정책이 없고(caller identity 서명만 한다), workload EKS Access Entry 는 access policy 없이 group `iris-console` 에만 매핑한다. group 의 권한은 `cluster-baseline` 의 ClusterRole(`pods` get·list, `pods/exec` get·create) 하나가 정한다. cluster-admin 이나 Argo 의 deploy role 을 재사용하지 않는다.
- **ValidatingAdmissionPolicy 로 범위를 한 번 더 좁힌다.** ClusterRole 은 클러스터 전체라서, group `iris-console` 의 exec 는 `svc-*` namespace 의 `app` 컨테이너만 허용한다. Gateway 가 잘못되거나 침해돼도 `kube-system`·`observability`(privileged) 로 번지지 않는다.
- **API 와 같은 host(`api.likelion.uk`)의 정확한 경로 `/v1/pods`·`/v1/exec` 로 노출한다.** 별도 Ingress 가 `group.order: -1` 로 API 의 `/` 보다 먼저 평가된다. 새 DNS·인증서·CORS origin 이 필요 없다.
- **Gateway 는 replica 1, `Recreate`.** 1회용 ticket 검사가 메모리라서 두 Pod 가 동시에 있으면 안 된다. chart schema 가 `replicas` 를 1 로 제한한다.
- **chart 의 기본값은 꺼짐이고, 클러스터 values 는 켠 채로 병합하되 Pod 는 digest 가 있어야 뜬다.** 운영자가 키 쌍과 Secret(`iris-console-ticket-signer`)을 미리 만들었고 공개키·workload endpoint/CA 는 이 PR 의 values 에 있다. Gateway digest 가 GitOps 에 없는 동안은 SA·ConfigMap 만 생기고 API 에도 Gateway 주소가 들어가지 않는다. workload baseline 에는 RBAC 객체가 inert 하게 먼저 들어간다. 이미지 digest 는 다른 플랫폼 컴포넌트처럼 GitOps `was.yaml` 이 정한다.

## 검토한 대안

- **Argo CD 터미널 프록시**: 코드는 적지만 `/terminal` 이 UI 내부 엔드포인트라 안정 API 가 아니고, Argo 의 넓은 권한에 exec 까지 얹힌다. Control API 가 Argo 를 부르지 않는 원칙(iris-was ADR 0029)도 깨진다. 제외.
- **Control API 에 클러스터 접근 부여**: iris-was 설계 문서 §7 의 컴포넌트별 권한 경계를 무너뜨린다. 제외.
- **namespace 별 RoleBinding**(서비스 chart 가 `svc-N` 에 만듦): 권한이 가장 좁지만 모든 사용자 서비스의 chart 버전을 올려야 하고 AppProject 허용 kind 와 배포 경로가 함께 바뀐다. ClusterRole + admission policy 로 같은 효과를 한 곳에서 낸다. 서비스 수가 늘어 Pod 목록 읽기 범위가 문제가 되면 다시 검토한다.
- **별도 호스트(`console.likelion.uk` 등)**: 경로 접두가 필요 없지만 DNS 가 Cloudflare 수동 작업이고 CORS·인증서 설정이 하나 더 는다. 같은 host 의 정확한 경로로 충분해서 제외.

## 영향

- 새 리소스: IAM role 1(권한 없음), Pod Identity association 1, workload Access Entry 1, workload ClusterRole·Binding·ValidatingAdmissionPolicy·Binding, management 의 Gateway Deployment·Service·Ingress·NetworkPolicy·SA·ConfigMap.
- [아키텍처](../architecture.md)의 "Build/Deploy Worker 에 앱 EKS Access Entry 를 주지 않는다"는 유지되고, Console Gateway 만 group 매핑 Access Entry 를 가진다.
- Gateway 를 배포·재시작하면 열린 콘솔이 끊긴다. 모든 namespace 의 Pod 목록·spec 읽기 권한은 남는다(Secret 은 아님).
- ALB idle timeout(60초, 그룹 공통)은 화면·Gateway 의 25초 ping 으로 넘긴다.

## 검증 사항

- 정적: `make helm-check`(Gateway fixture·잘못된 입력 거절·공개키 헤더 제한·경로·`group.order`·단일 replica·baseline RBAC·AppProject 허용 kind·꺼진 상태의 렌더가 이전과 같음), `make tf-test`(role 신뢰 조건·Access Entry 가 group 만 매핑·access policy 없음·CI 권한 경계).
- 배포 후 확인(미확인): Pod Identity 로 서명한 토큰이 workload API 에서 인증됨, `pods/exec` 에 필요한 verb(`get`/`create`), 정책이 `svc-*` 밖과 `app` 이외 컨테이너를 거절함, ALB 가 두 경로를 Gateway 로 보내고 WebSocket 이 25초 ping 으로 유지됨.
