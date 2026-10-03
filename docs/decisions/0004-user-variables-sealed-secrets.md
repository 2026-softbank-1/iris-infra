# 0004: 사용자 환경변수는 Sealed Secrets 로 앱에 전달

상태: 제안. main merge 와 `make bootstrap`(새 SHA) 이후 적용되며, 실제 클러스터 동작은 배포 후 검증 전까지 확인된 것이 아니다.

## 배경

사용자가 서비스에 등록한 환경변수(DB 주소, API 키 등)를 앱 컨테이너에 넘겨야 한다. iris-was 는 값을 암호화해 저장하고 배포 요청마다 스냅샷을 남긴다. 앱까지의 전달 경로는 없었다.

- Deploy Worker 는 Prod 클러스터에 접근하지 않고 GitOps 저장소에 values 만 커밋한다([ADR 0002](0002-gitops-deployment.md)).
- 평문 변수는 Git 에 넣을 수 없다. `iris-service` values schema 는 모르는 키를 거절한다.
- 롤백은 GitOps revert commit 이다. 되돌린 values 만으로 이전 변수가 다시 만들어져야 한다.

## 결정

Sealed Secrets 를 workload 클러스터에 설치하고, Deploy Worker 가 봉인한 값을 values 로 넘긴다.

- **controller**: addon Application `sealed-secrets`(workload 만, `kube-system`, chart 2.20.0 / controller 0.40.0, 이미지 digest 고정). 객체 이름은 `sealed-secrets-controller`(kubeseal 기본값). 키 자동 교체는 끈다(`keyrenewperiod: "0"`). Deploy Worker 가 인증서 하나로 봉인하므로 키가 바뀌면 안 된다.
- **values 계약**: `variables.name`(Secret 이름) + `variables.encryptedData`(변수별 봉인 값). `iris.serviceName`·`iris.targetName`·`iris.deploymentId` 는 env `IRIS_SERVICE_NAME`·`IRIS_TARGET_NAME`·`IRIS_DEPLOYMENT_ID` 가 된다. 모두 선택이라 기존 Deploy Worker 출력도 그대로 렌더링된다.
- **chart**: `SealedSecret`(sync-wave -1)과 `envFrom: secretRef` 를 `variables` 가 있을 때만 만든다. `env` 가 `envFrom` 보다 우선하므로 `PORT`·`IRIS_*` 는 덮어쓸 수 없고 schema 도 그 이름을 거절한다.
- **봉인 범위**: strict(namespace `svc-{service_id}` + `variables.name`). 다른 namespace·이름으로 옮긴 SealedSecret 은 풀리지 않는다.
- **Secret 이름은 release 마다 새로 정한다**(`vars-r{release_id}`). 새 Secret 이 먼저 만들어진 뒤 Pod 이 뜨고, 이전 Secret 은 `PruneLast` 로 나중에 지워져 롤아웃 중 재시작하는 이전 Pod 이 깨지지 않는다. 롤백은 이전 이름과 암호문이 values 와 함께 돌아온다.
- **AppProject** `iris-svc-project` 허용 목록에 `bitnami.com/SealedSecret` 을 추가한다. Argo 의 workload 접근은 이미 cluster-admin Access Entry 라 ClusterRole 을 더하지 않는다.
- chart 는 `iris-service-0.6.0`, `chartRevision` 을 함께 올린다(`make helm-check` 가 둘이 같음을 검사한다).

## 검토한 대안

- 평문 values: Git 에 비밀이 남는다. 제외.
- External Secrets + Secrets Manager: Worker IAM·ESO·서비스별 secret 비용이 추가되고 롤백 때 secret 버전을 따로 맞춰야 한다.
- Deploy Worker 가 Secret 을 직접 생성: Worker 가 Prod 에 접근해야 한다. 원칙에 어긋난다.

## 영향

- 새 addon 하나(controller 1 replica). 키가 단일 장애점이므로 백업이 필수다([runbook](../runbooks/sealed-secrets.md)). 키를 잃으면 저장된 SealedSecret 을 풀 수 없고 서비스를 다시 배포해 새 키로 봉인해야 한다.
- Deploy Worker 에 controller 공개 인증서가 필요하다(비밀이 아니다). 전달 방식은 iris-was 가 정한다.
- 이름이 release 마다 달라 변수가 있는 서비스는 배포마다 Secret 이 하나씩 만들어졌다가 지워진다.

## 검증 사항

- 정적: `make helm-check`(고정 버전·digest 렌더, schema 거절, SealedSecret·envFrom 렌더, AppProject 허용 kind), `scripts/tests/test-eks-ops.py`.
- 배포 후 확인: controller Ready, 임의 SealedSecret 이 Secret 으로 풀림, 변수가 있는 서비스가 값을 환경변수로 읽음, 롤백 시 이전 값 복원. 이 항목은 배포 전까지 확인되지 않았다.
