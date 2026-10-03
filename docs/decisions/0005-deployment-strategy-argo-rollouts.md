# 0005: 사용자 서비스 배포 방식은 Argo Rollouts 로 고른다

상태: 제안. controller 는 main merge 후 Argo 가 AWS workload 에 설치하고, chart 0.7.0 은 tag 와 `chartRevision` PR 뒤에 AWS 서비스에 적용된다. on-prem 은 0.6.0 에 남는다(2026-10-04 변경). 실제 클러스터 동작은 적용 후 검증 전까지 확인된 것이 아니다.

## 배경

사용자가 서비스마다 롤링(기본)·카나리·블루그린을 고르게 한다(iris-was SPEC `deployment-strategy-selection`). `iris-service` 0.6.0 은 일반 `Deployment` 의 RollingUpdate 만 쓰고, 트래픽 가중치 라우팅(ALB·Traefik·Istio)은 없다. AWS(공유 ALB)와 on-prem(Traefik) 두 타깃이 같은 chart 를 쓴다.

## 결정

- **Argo Rollouts**(chart 2.43.5 / controller v1.10.0, 이미지 digest 고정)를 AWS workload 에 addon Application 으로 설치한다.
- **on-prem 은 지원하지 않는다**(2026-10-04 변경). on-prem Argo 계정에 CRD·ClusterRole 권한을 주지 않아 controller 를 수동으로 따로 운영해야 하므로, on-prem ApplicationSet 은 자체 pin `services.onprem.chartRevision` 으로 Deployment 기반 `iris-service-0.6.0`(롤링만)을 계속 쓴다. 0.6.0 schema 가 `deploymentStrategy` 를 거절하므로 iris-was 는 on-prem 타깃에 그 키를 쓰지 않는다. 나중에 지원하는 절차는 [runbook](../runbooks/argo-rollouts.md) 에 있다.
- chart 0.7.0 은 방식과 상관없이 `Rollout` 하나로 앱을 띄운다. 방식 전환이 values `deploymentStrategy` 변경뿐이 되도록 롤링도 Rollout(steps 없는 canary)이다.
- **Pod 비율 방식**: traffic routing 을 쓰지 않는다. 카나리는 `setWeight: floor(100/replicas)` 로 새 Pod 1개 → 60초 pause → 나머지 롤링, 블루그린은 기존 Service `app` 을 activeService 로 하고 preview Service 없이 autoPromotionSeconds 30·scaleDownDelaySeconds 30. 단계·시간은 chart 가 고정한다.
- replicas 가 2 미만이면 chart 가 `ROLLING` 으로 렌더링한다. 모든 방식에 `progressDeadlineSeconds = health.timeoutSeconds`, `progressDeadlineAbort: true`.
- **전환**: 0.6.0 Deployment 는 services ApplicationSet 의 `PruneLast=true` 로 새 Rollout 이 Healthy 가 된 뒤 지운다. AppProject 는 Rollout 과 Deployment 를 모두 허용한다.
- **배포 순서**: 0.7.0 은 chart PR merge → tag → AWS `chartRevision` PR 순서로 반영했다. `make helm-check` 는 AWS pin 이 현재 chart tag 와 같고 on-prem pin 이 `iris-service-0.6.0` 인지 검사한다.

## 검토한 대안

- Deployment 유지 + 카나리용 두 번째 Deployment: 상태·정리를 Deploy Worker 가 맡아야 하고 블루그린은 Service 전환을 직접 해야 한다.
- ALB·Traefik 가중치 라우팅: 타깃마다 구현이 달라지고 SPEC 범위 밖이다.
- 서비스별 chart 버전: 전역 동시 전환은 피하지만 ApplicationSet·Worker 변경이 크다(SPEC D-11). 타깃별 pin(AWS·on-prem)만 둔다.
- on-prem 에도 운영자 Helm 설치로 controller 를 둔다: 처음 안이었으나 수동 운영 부담 때문에 뺐다.

## 영향

- 새 addon(controller 1 replica). controller 가 멈추면 새 배포가 진행하지 않지만 떠 있는 Pod 는 그대로다. controller ClusterRole 은 클러스터 전체 Secret 읽기를 포함한다(upstream 기본, AnalysisTemplate 용).
- chart 0.7.0 반영 때 모든 AWS 서비스가 동시에 Deployment → Rollout 으로 바뀌며 잠시 Pod 가 2배가 된다. on-prem 서비스는 그대로다.
- 0.7.0 이후 chart 변경은 on-prem 에 자동으로 가지 않는다. 필요하면 0.6.x 를 따로 내거나 on-prem 을 Rollout 으로 옮긴다. 블루그린은 배포마다 최대 2배다.
- AWS 블루그린은 전환 직후 ALB 가 새 target 의 첫 health check 를 기다리는 동안 짧게 503 을 줄 수 있다. 블루그린 서비스의 TargetGroup 만 health check 주기를 5초로 줄여 구간을 줄이고, 없애는 것(ALB traffic routing)은 범위 밖이다. 롤링·카나리는 readiness gate 로 0.6.0 과 같다. 상세와 완화책: [runbook](../runbooks/argo-rollouts.md#블루그린과-aws-alb).
- 롤링·카나리에서 블루그린으로 바꾼 뒤 첫 블루그린 배포는 Service 에 hash selector 가 없어 롤링처럼 섞여 들어간다(controller fast-track).

## 검증 사항

- 정적: `make helm-check`(controller 고정 버전·digest·CRD·RBAC, 방식×replicas 0~10 렌더, 카나리 Pod 수 계산, schema 거절, AppProject 허용 kind, chartRevision 규칙), `scripts/tests/test-eks-ops.py`.
- 적용 후 확인: controller Ready, 전환 중 서비스 200 유지, Deployment 삭제, 카나리 새 Pod 1개·60초, 블루그린 전환 시 응답 코드, 실패 시 abort 와 자동 rollback. 이 항목은 적용 전까지 확인되지 않았다.
