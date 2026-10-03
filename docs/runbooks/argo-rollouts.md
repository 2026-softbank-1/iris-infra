# Argo Rollouts (사용자 서비스 배포 방식)

`iris-service` chart 0.7.0 은 사용자 앱을 `Deployment` 대신 Argo Rollouts `Rollout` 으로 띄웁니다. 배포 방식(롤링·카나리·블루그린)은 values `deploymentStrategy` 하나로 고릅니다([ADR 0005](../decisions/0005-deployment-strategy-argo-rollouts.md)). 아래 운영 확인 명령은 적용 후 실행하는 절차이며, `make helm-check` 의 렌더 검사는 실제 클러스터 동작을 보장하지 않습니다.

## controller

| 클러스터 | 설치 방법 | 위치 |
|---|---|---|
| AWS workload | addon Application `iris-workload-argo-rollouts`(wave 1, `kube-system`) | `clusters/aws-dev-workload/values/argo-rollouts.yaml` |
| on-prem | 운영자가 같은 chart·values 로 직접 Helm 설치 | `clusters/onprem-workload/values/argo-rollouts.yaml` |

- 버전: chart `argo-rollouts` 2.43.5 / controller v1.10.0(`helm/versions.json`, 이미지 digest 는 `helm/images.lock.json`).
- CRD 를 chart 가 함께 설치하고(`installCRDs`), chart 를 지워도 CRD 는 남깁니다(`keepCRDs`). Rollout 이 남아 있는 동안 CRD 를 지우면 앱이 함께 지워집니다.
- controller 1 replica, dashboard·metrics Service 없음, traffic router RBAC(`providerRBAC`) 없음. 카나리는 Pod 비율 방식이라 ALB·Traefik·Istio 를 건드리지 않습니다.
- on-prem 은 Argo 의 `iris-argocd` ServiceAccount 에 CRD·ClusterRole 권한을 주지 않으므로(배포 역할은 `svc-*` 앱 리소스만 씁니다) Argo 가 controller 를 설치하지 않습니다. 두 values 파일은 같아야 하며 `make helm-check` 가 검사합니다.

### on-prem 설치

on-prem 클러스터 관리자 kubeconfig 로 실행합니다. 이 저장소의 리뷰된 main 을 checkout 한 상태에서 합니다.

```bash
export KUBECONFIG=<on-prem 관리자 kubeconfig>
helm upgrade --install argo-rollouts argo-rollouts \
  --repo https://argoproj.github.io/argo-helm --version 2.43.5 \
  --namespace kube-system -f clusters/onprem-workload/values/argo-rollouts.yaml --wait
kubectl apply -f clusters/onprem-workload/argocd-service-deployer.yaml   # Rollout 쓰기 권한 추가
kubectl -n kube-system rollout status deploy/argo-rollouts
kubectl get crd rollouts.argoproj.io
kubectl auth can-i create rollouts.argoproj.io -n svc-1 --as system:serviceaccount:iris-onprem-test:iris-argocd
```

노드 아키텍처는 이미지 index 가 amd64·arm64 를 모두 담고 있어 상관없습니다. 버전을 올릴 때는 `helm/versions.json`·`images.lock.json`·두 values 파일을 같은 PR 에서 바꾸고, merge 뒤 위 명령을 다시 실행합니다.

### AWS 확인

```bash
K="--kubeconfig .generated/kubeconfig-aws-dev-workload.json --context iris-dev-workload"
kubectl $K -n kube-system rollout status deploy/argo-rollouts
kubectl $K get crd rollouts.argoproj.io
```

`make bootstrap` 은 `iris-workload-argo-rollouts` Application 의 Synced/Healthy 도 기다립니다.

## chart 0.7.0 반영 순서

`services.chartRevision` 은 모든 서비스(AWS·on-prem ApplicationSet)가 같이 쓰는 값입니다. tag 가 없는 상태에서 main 에 올라가면 root 가 main 을 추적하므로 모든 서비스 sync 가 실패합니다. 그래서 chart 를 merge 한 PR 에서는 chartRevision 을 올리지 않습니다(`make helm-check` 는 chartRevision 이 현재 chart 또는 그 이전 버전 tag 이면 통과하고 안내를 출력합니다).

1. controller·chart 0.7.0 PR 을 main 에 merge 합니다. 서비스는 계속 `iris-service-0.6.0` 을 씁니다.
2. AWS: `iris-workload-argo-rollouts` Application 이 Synced/Healthy 인지, `rollouts.argoproj.io` CRD 가 있는지 확인합니다. live root 가 SHA 에 고정돼 있으면 merge SHA 또는 main 으로 `make bootstrap CLUSTER=aws-dev-management` 를 다시 실행합니다.
3. on-prem: [위 명령](#on-prem-설치)으로 controller 를 설치하고 배포 역할을 다시 적용합니다. 이 단계 전에 chartRevision 을 올리면 on-prem 서비스가 `Rollout` 을 만들지 못해 sync 에 실패합니다(기존 Deployment 는 남아 서비스는 계속됩니다).
4. merge commit 에 tag 를 만들고 push 합니다. 이전 tag 와 같은 annotated tag 입니다.

   ```bash
   git fetch origin && git tag -a iris-service-0.7.0 <merge commit SHA> -m "iris-service chart 0.7.0" && git push origin iris-service-0.7.0
   ```

5. 용량을 확인합니다. 전환 중에는 서비스마다 이전 Deployment Pod 와 새 Rollout Pod 가 함께 떠 Pod 수가 잠시 2배가 되고, 모든 서비스가 동시에 바뀝니다.
6. 별도 PR 로 `helm/gitops/values.yaml` 의 `services.chartRevision` 을 `iris-service-0.7.0` 으로 올려 merge 합니다.
7. 확인: 서비스마다 `svc-{id}` Application 이 Synced/Healthy, `kubectl get rollout,deploy -n svc-<id>` 에서 Rollout `app` 만 남고 Deployment 가 없어야 합니다. 전환하는 동안 서비스 URL 이 계속 200 을 응답하는지 반복 요청으로 봅니다.
8. 그 뒤 iris-was 를 배포하고 `DEPLOYMENT_STRATEGY_ENABLED=true` 를 켭니다. 0.6.0 schema 는 `deploymentStrategy` 키를 거절합니다.

### 무중단 전환 (Deployment → Rollout)

- Rollout 은 Deployment 와 같은 selector 라벨을 씁니다. 각 controller 는 자기 소유 ReplicaSet 만 다루고(`pod-template-hash` / `rollouts-pod-template-hash`), Service `app` 은 전환 동안 두 쪽 Pod 를 모두 엔드포인트로 둡니다. 이미지·values 가 같아 섞여도 응답이 같습니다.
- 처음 만들어지는 Rollout 은 단계 없이 한 번에 replicas 만큼 뜹니다. AWS 에서는 새 Pod 가 Service 에 잡힌 채로 만들어져 ALB readiness gate 를 받으므로, ALB target 이 healthy 가 된 뒤에 Ready 가 됩니다.
- services ApplicationSet 의 `PruneLast=true` 가 0.6.0 Deployment 삭제를 sync 의 마지막 단계로 미룹니다. Argo CD 는 이 단계를 나머지 리소스(Rollout 포함, Argo 기본 Rollout health 사용)가 Healthy 가 된 뒤에만 실행합니다. Rollout 이 Healthy 가 되지 못하면(용량 부족 등) sync 가 실패하고 Deployment 는 지워지지 않아 서비스는 그대로 돕니다. 이때 원인을 해결하고 Application 을 수동 sync 합니다(automated sync 는 같은 revision 을 다시 시도하지 않습니다).
- 별도 hook·sync-wave 를 더하지 않은 이유: PruneLast 는 이미 SealedSecret 이름 교체에 쓰고 있고, 지울 리소스만 늦추므로 다른 리소스 순서를 바꾸지 않습니다.

### 되돌리기

1. iris-was `DEPLOYMENT_STRATEGY_ENABLED=false` 로 바꿉니다.
2. **`deploymentStrategy` 가 남은 values 파일이 없게 합니다.** values 는 다음 배포 때까지 GitOps 저장소에 남고, 0.6.0 schema 는 그 키를 거절해 해당 서비스 sync 가 실패합니다. 키가 있는 서비스는 플래그를 끈 뒤 다시 배포합니다.

   ```bash
   grep -l deploymentStrategy iris-gitops-environments/services/*/*/values.yaml
   ```

3. chartRevision 을 `iris-service-0.6.0` 으로 되돌리는 PR 을 merge 합니다. Argo 가 Deployment 를 만들고 Rollout 은 PruneLast 로 마지막에 지웁니다. 블루그린이 쓰던 Service 의 hash selector 는 Rollout 이 지워지면 controller 가 걷어 냅니다. controller 는 Rollout 이 모두 사라진 뒤에 지웁니다.

## 블루그린과 AWS ALB

공유 ALB 는 `target-type: ip` 이고 서비스 namespace 에 readiness gate 주입이 켜져 있습니다. 코드와 AWS 문서로 확인한 동작이며 실제 클러스터에서 재현하지 않았습니다.

- **롤링·카나리**: 새 Pod 가 만들어질 때부터 Service `app` 에 잡히므로 LBC 가 readiness gate 를 넣고, ALB target 이 healthy 가 된 뒤에 Ready 가 됩니다. 이전 Pod 는 그 뒤에 내려가 0.6.0 과 같습니다.
- **블루그린**: 새 묶음은 Service selector(`rollouts-pod-template-hash` = 이전 묶음) 밖에서 만들어져 readiness gate 를 받지 않고 TargetGroup 에도 없습니다. 전환하면 LBC 가 새 Pod 를 등록하고(`initial`) 이전 Pod 를 해제합니다(`draining`). ALB 는 `initial` target 에 요청을 보내지 않고(첫 health check 통과 필요) `draining` target 에도 새 요청을 보내지 않으므로, 전환 직후 등록 시간 + health check 한 주기(LBC 기본 15초) 정도 새 요청이 503 이 될 수 있습니다. `scaleDownDelaySeconds: 30` 은 이전 Pod 가 진행 중 요청을 끝내게 할 뿐 이 구간을 막지 못합니다.
- controller 의 `awsVerifyTargetGroup` 은 새 Pod IP 가 등록됐는지만 보고 이전 묶음 축소를 늦추며, health 는 보지 않고 controller 에 AWS 권한이 필요해 쓰지 않습니다.
- 이 PR 은 이 구간을 고치지 않습니다. 줄이려면 서비스 Ingress 에 `alb.ingress.kubernetes.io/healthcheck-interval-seconds: "5"` 를 주어 구간을 몇 초로 줄이거나, 없애려면 TargetGroup 두 개로 가중치를 바꾸는 ALB traffic routing 이 필요합니다(SPEC 범위 밖).
- on-prem Traefik 은 Service 엔드포인트를 바로 따라가 이 구간이 없습니다.
