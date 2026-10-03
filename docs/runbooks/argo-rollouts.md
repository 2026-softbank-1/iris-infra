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
