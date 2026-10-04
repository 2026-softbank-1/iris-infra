# Argo Rollouts (사용자 서비스 배포 방식)

`iris-service` chart 0.7.0 은 사용자 앱을 `Deployment` 대신 Argo Rollouts `Rollout` 으로 띄웁니다. 배포 방식(롤링·카나리·블루그린)은 values `deploymentStrategy` 하나로 고릅니다([ADR 0005](../decisions/0005-deployment-strategy-argo-rollouts.md)). 아래 운영 확인 명령은 적용 후 실행하는 절차이며, `make helm-check` 의 렌더 검사는 실제 클러스터 동작을 보장하지 않습니다.

## 타깃별 chart

| 타깃 | chart pin (`helm/gitops/values.yaml`) | 앱 리소스 | 배포 방식 |
|---|---|---|---|
| AWS workload | `services.chartRevision` = `iris-service-0.9.0` | `Rollout` | 롤링·카나리·블루그린 |
| on-prem | `services.onprem.chartRevision` = `iris-service-0.6.0` | `Deployment` | 롤링만 |

- on-prem 에는 Argo Rollouts controller 를 설치하지 않습니다. on-prem ApplicationSet 은 자기 chart pin 으로 Deployment 기반 0.6.0 을 계속 씁니다. `make helm-check` 가 AWS pin = 현재 chart tag, on-prem pin = `iris-service-0.6.0` 을 검사합니다.
- 0.6.0 schema 는 `deploymentStrategy` 키를 거절합니다. **iris-was Deploy Worker 는 on-prem 타깃의 values 에 이 키를 쓰지 않습니다.** 키가 들어가면 그 서비스의 sync 가 실패합니다(떠 있는 Pod 는 그대로).
- 0.7.0 이후 chart 변경(버그 수정 등)은 on-prem 에 자동으로 가지 않습니다. on-prem 에도 필요하면 0.6.x 를 따로 내거나 아래처럼 on-prem 을 Rollout 으로 옮깁니다.

## controller (AWS)

addon Application `iris-workload-argo-rollouts`(wave 1, `kube-system`, values `clusters/aws-dev-workload/values/argo-rollouts.yaml`).

- 버전: chart `argo-rollouts` 2.43.5 / controller v1.10.0(`helm/versions.json`, 이미지 digest 는 `helm/images.lock.json`).
- CRD 를 chart 가 함께 설치하고(`installCRDs`), chart 를 지워도 CRD 는 남깁니다(`keepCRDs`). Rollout 이 남아 있는 동안 CRD 를 지우면 앱이 함께 지워집니다.
- controller 1 replica, dashboard·metrics Service 없음, traffic router RBAC(`providerRBAC`) 없음. 카나리는 Pod 비율 방식이라 ALB·Istio 를 건드리지 않습니다.

```bash
K="--kubeconfig .generated/kubeconfig-aws-dev-workload.json --context iris-dev-workload"
kubectl $K -n kube-system rollout status deploy/argo-rollouts
kubectl $K get crd rollouts.argoproj.io
```

`make bootstrap` 은 `iris-workload-argo-rollouts` Application 의 Synced/Healthy 도 기다립니다.

## 다른 클러스터에 controller 설치 (로컬·나중의 on-prem)

Argo 가 설치하지 않는 클러스터는 관리자 kubeconfig 로 같은 고정 버전을 직접 설치합니다. 이 저장소의 리뷰된 main 을 checkout 한 상태에서 합니다. 이미지 index 는 amd64·arm64 를 모두 담고 있습니다.

```bash
export KUBECONFIG=<대상 클러스터 관리자 kubeconfig>
helm upgrade --install argo-rollouts argo-rollouts \
  --repo https://argoproj.github.io/argo-helm --version 2.43.5 \
  --namespace kube-system -f clusters/aws-dev-workload/values/argo-rollouts.yaml --wait
kubectl -n kube-system rollout status deploy/argo-rollouts
kubectl get crd rollouts.argoproj.io
```

### on-prem 지원을 나중에 추가하려면

1. 이 저장소: `clusters/onprem-workload/values/argo-rollouts.yaml` 을 두고(AWS values 와 같게 유지하는 검사를 `check-helm.py` 에 더합니다), [배포 역할](../../clusters/onprem-workload/argocd-service-deployer.yaml)에 `argoproj.io` `rollouts` 의 create·update·patch·delete 를 더합니다. on-prem Argo 계정에는 CRD·ClusterRole 권한을 주지 않으므로 controller 는 위 Helm 명령으로 운영자가 설치합니다.
2. on-prem 에 controller 를 설치하고 배포 역할을 다시 적용한 뒤 `kubectl auth can-i create rollouts.argoproj.io -n svc-1 --as system:serviceaccount:iris-onprem-test:iris-argocd` 로 확인합니다.
3. 별도 PR 로 `services.onprem.chartRevision` 을 AWS 와 같은 tag 로 올리고 `check-helm.py` 의 `ONPREM_CHART_REVISION` 규칙을 바꿉니다. 기존 Deployment 는 AWS 와 같이 PruneLast 로 Rollout 이 Healthy 가 된 뒤 지워집니다.
4. iris-was 가 on-prem 타깃에도 `deploymentStrategy` 를 쓰게 합니다. Traefik 은 Service 엔드포인트를 바로 따라가 블루그린 전환 공백(아래 ALB 절)이 없습니다.

## chart 반영 순서

AWS 와 on-prem 은 chart pin 이 따로입니다. `make helm-check` 는 AWS pin 이 현재 `Chart.yaml` version 의 tag 와 같아야 통과하므로 chart version 과 pin 을 같은 PR 에서 올립니다. root 가 main 을 추적하므로 merge 직후 tag 를 push 하기 전까지 AWS 서비스 Application 이 ComparisonError 가 됩니다(떠 있는 Pod 는 그대로이고, tag 가 생기면 다음 refresh 에 풀릴 것으로 봅니다. 실측하지 않았습니다). merge 하고 바로 tag 를 push 합니다.

0.7.0 은 다음 순서로 반영했습니다.

1. controller·chart 0.7.0 PR(#71)을 merge 하고 merge commit 에 annotated tag `iris-service-0.7.0` 을 push 했습니다. 서비스는 그때까지 0.6.0 이었습니다.

   ```bash
   git fetch origin && git tag -a iris-service-<version> <merge commit SHA> -m "iris-service chart <version>" && git push origin iris-service-<version>
   ```

2. AWS `iris-workload-argo-rollouts` Application 이 Synced/Healthy 이고 `rollouts.argoproj.io` CRD 가 있는지 확인합니다. live root 가 SHA 에 고정돼 있으면 main 으로 `make bootstrap CLUSTER=aws-dev-management` 를 다시 실행합니다.
3. 용량을 확인합니다. 전환 중에는 AWS 서비스마다 이전 Deployment Pod 와 새 Rollout Pod 가 함께 떠 Pod 수가 잠시 2배가 되고, 모든 AWS 서비스가 동시에 바뀝니다.
4. AWS `services.chartRevision` 을 `iris-service-0.7.0` 으로 올리고 on-prem 을 `services.onprem.chartRevision: iris-service-0.6.0` 으로 고정하는 PR 을 merge 합니다.
5. 확인: AWS 서비스마다 `svc-{id}` Application 이 Synced/Healthy, `kubectl get rollout,deploy -n svc-<id>` 에서 Rollout `app` 만 남고 Deployment 가 없어야 합니다. 전환하는 동안 서비스 URL 이 계속 200 을 응답하는지 반복 요청으로 봅니다. on-prem 서비스는 Deployment 그대로입니다.
6. 그 뒤 iris-was 를 배포하고 `DEPLOYMENT_STRATEGY_ENABLED=true` 를 켭니다(AWS 타깃에만 키를 씁니다).

### 무중단 전환 (Deployment → Rollout)

- Rollout 은 Deployment 와 같은 selector 라벨을 씁니다. 각 controller 는 자기 소유 ReplicaSet 만 다루고(`pod-template-hash` / `rollouts-pod-template-hash`), Service `app` 은 전환 동안 두 쪽 Pod 를 모두 엔드포인트로 둡니다. 이미지·values 가 같아 섞여도 응답이 같습니다.
- 처음 만들어지는 Rollout 은 단계 없이 한 번에 replicas 만큼 뜹니다. AWS 에서는 새 Pod 가 Service 에 잡힌 채로 만들어져 ALB readiness gate 를 받으므로, ALB target 이 healthy 가 된 뒤에 Ready 가 됩니다.
- services ApplicationSet 의 `PruneLast=true` 가 0.6.0 Deployment 삭제를 sync 의 마지막 단계로 미룹니다. Argo CD 는 이 단계를 나머지 리소스(Rollout 포함, Argo 기본 Rollout health 사용)가 Healthy 가 된 뒤에만 실행합니다. Rollout 이 Healthy 가 되지 못하면(용량 부족 등) sync 가 실패하고 Deployment 는 지워지지 않아 서비스는 그대로 돕니다. 이때 원인을 해결하고 Application 을 수동 sync 합니다(automated sync 는 같은 revision 을 다시 시도하지 않습니다).
- 별도 hook·sync-wave 를 더하지 않은 이유: PruneLast 는 이미 SealedSecret 이름 교체에 쓰고 있고, 지울 리소스만 늦추므로 다른 리소스 순서를 바꾸지 않습니다.

### 되돌리기

AWS chartRevision 을 0.6.0 으로 되돌리기 **전에** 1·2 를 끝냅니다. on-prem 은 계속 0.6.0 이라 할 일이 없습니다.

1. iris-was `DEPLOYMENT_STRATEGY_ENABLED=false` 로 바꿉니다.
2. **`deploymentStrategy` 가 남은 values 파일이 없게 합니다.** values 는 다음 배포 때까지 GitOps 저장소에 남고, 0.6.0 schema 는 그 키를 거절해 해당 서비스 sync 가 실패합니다. 키가 있는 서비스는 플래그를 끈 뒤 다시 배포합니다.

   ```bash
   grep -l deploymentStrategy iris-gitops-environments/services/*/*/values.yaml
   ```

3. AWS `services.chartRevision` 을 `iris-service-0.6.0` 으로 되돌리는 PR 을 merge 합니다(`make helm-check` 의 pin 규칙도 함께 바꿉니다). Argo 가 Deployment 를 만들고 Rollout 은 PruneLast 로 마지막에 지웁니다. 블루그린이 쓰던 Service 의 hash selector 는 Rollout 이 지워지면 controller 가 걷어 냅니다. controller 는 Rollout 이 모두 사라진 뒤에 지웁니다.

### Pod 종료 대기 (0.7.1)

운영 E2E 에서 Deployment → Rollout 전환과 카나리 배포 때 이전 Pod 의 `Killing` 이벤트 4~5초 뒤 요청이 한 번씩 끊겼습니다(블루그린은 이전 Pod 가 이미 Service 밖이라 없었습니다). Pod 가 Endpoint 에서 빠진 뒤 LBC 가 ALB target 을 해제해 반영되기 전에 앱이 SIGTERM 으로 먼저 내려갔기 때문입니다. 0.7.1 부터 컨테이너에 preStop `sleep: {seconds: 15}`(kubelet sleep action, 이미지에 shell 이 필요 없음)와 `terminationGracePeriodSeconds: 45` 를 둬 SIGTERM 을 15초 늦추고 앱에는 기본 30초를 남깁니다. 그만큼 이전 Pod 가 늦게 사라져 배포가 Pod 마다 15초쯤 길어집니다. Rollout CRD(v1.10.0)의 pod schema 는 `preStop.sleep` 을 포함해 이 필드를 지우지 않으며 `make helm-check` 가 검사합니다. on-prem(0.6.0)에는 적용되지 않습니다.

## 블루그린과 AWS ALB

공유 ALB 는 `target-type: ip` 이고 서비스 namespace 에 readiness gate 주입이 켜져 있습니다. 아래는 controller 소스(v1.10.0)와 AWS·LBC 문서로 확인한 동작이며 실제 클러스터에서 재현하지 않았습니다.

- **롤링·카나리**: 새 Pod 가 만들어질 때부터 Service `app` 에 잡히므로 LBC 가 readiness gate 를 넣고, ALB target 이 healthy 가 된 뒤에 Ready 가 됩니다. 이전 Pod 는 그 뒤에 내려가 0.6.0 과 같습니다.
- **블루그린**: 새 묶음은 Service selector(`rollouts-pod-template-hash` = 이전 묶음) 밖에서 만들어져 readiness gate 를 받지 않고 TargetGroup 에도 없습니다. 전환하면 LBC 가 새 Pod 를 등록하고(`initial`) 이전 Pod 를 해제합니다(`draining`). 그 사이 새 요청을 받을 target 이 없어 등록 시간 + 첫 health check 까지 503 이 날 수 있습니다. `scaleDownDelaySeconds: 30` 은 이전 Pod 가 진행 중 요청을 끝내게 할 뿐 이 구간을 막지 못합니다.
- **AWS 문서 근거**
  - 새 target: "The load balancer starts routing traffic to a newly registered target as soon as the registration process completes and the target passes the first initial health check, irrespective of the configured threshold." ([Target groups – Registered targets](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html#registered-targets)). healthy threshold 와 상관없이 첫 health check 한 번이 기준이므로 구간 길이는 점검 주기가 정합니다.
  - 해제한 target: "The load balancer stops routing requests to a target as soon as it is deregistered. The target enters the `draining` state until in-flight requests have completed." (같은 문서).
  - fail-open: "If a target group contains only unhealthy registered targets, the load balancer routes requests to all those targets, regardless of their health status." ([Health checks](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html)), routing failover 도 "sends traffic to all targets that are available to the load balancer node, including unhealthy targets" 로만 적혀 있습니다([Target group health](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html#target-group-health)). 같은 문서는 "Before a target can receive requests from the load balancer, it must pass the initial health checks." 라고 하고 `initial` 은 `unhealthy` 와 다른 상태입니다. **문서상 fail-open 은 `unhealthy` target 에만 해당하고 `initial` target 을 포함한다는 근거는 없습니다.** 그래서 이 구간을 fail-open 이 덮어 준다고 가정하지 않습니다(실측하지 않았습니다).
- **완화(적용함)**: chart 는 실제 렌더링 방식이 `BLUE_GREEN`(replicas 2 이상)일 때만 Ingress 에 `alb.ingress.kubernetes.io/healthcheck-interval-seconds: "5"`(최솟값)·`healthcheck-timeout-seconds: "4"`·`healthy-threshold-count: "2"` 를 붙입니다. 공백이 등록 시간 + 최대 약 5초로 줄어듭니다. timeout 은 LBC 기본값(5초)이 interval 과 같아지지 않게 4초로 둡니다. LBC 문서에서 이 annotation 들은 Location `Ingress,Service`, MergeBehavior `N/A` 라 IngressGroup 에서 합쳐지지 않고 그 Ingress 의 TargetGroup 에만 적용됩니다([LBC Ingress annotations](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/ingress/annotations/)). 공유 group `iris-service-external` 의 다른 서비스와 롤링·카나리 서비스의 TargetGroup 은 기본값(15초) 그대로입니다. 방식을 바꾸면 LBC 가 그 TargetGroup 의 health check 설정만 고칩니다.
- controller 의 `awsVerifyTargetGroup` 은 새 Pod IP 가 등록됐는지만 보고 이전 묶음 축소를 늦추며, health 는 보지 않고 controller 에 AWS 권한이 필요해 쓰지 않습니다.
- **근본 해결(범위 밖)**: 공백을 없애려면 TargetGroup 두 개에 가중치를 주는 ALB traffic routing(Rollouts `trafficRouting.alb`)이 필요합니다. SPEC 이 트래픽 가중치 라우팅을 제외해 이번에는 하지 않습니다.
- on-prem 은 0.6.0(롤링만)이라 해당하지 않습니다.

## 블루그린의 첫 배포

롤링·카나리에서 블루그린으로 바꾼 뒤(또는 Deployment 에서 Rollout 으로 옮긴 직후) **첫** 블루그린 배포는 롤링처럼 섞입니다. Service `app` 에 아직 `rollouts-pod-template-hash` selector 가 없어 새 Pod 가 Ready 가 되는 대로 이전 Pod 와 함께 트래픽을 받고, controller 는 새 묶음이 다 뜨면 pause 없이 selector 를 새 묶음으로 바꿉니다(`isBlueGreenFastTracked`). 두 번째 블루그린 배포부터 전환 전까지 이전 묶음만 트래픽을 받습니다. 블루그린에서 다른 방식으로 바꾸면 controller 가 그 selector 를 지워 Service 가 다시 모든 앱 Pod 를 고릅니다.
