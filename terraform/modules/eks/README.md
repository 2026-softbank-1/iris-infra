# EKS module

두 root에서 재사용하며 독립 apply하지 않습니다. EKS1.35 private API/API authentication/creator admin off/standard support, AL2023 x86_64 release1.35.8-20260930, AZ별 MNG(min/desired/max1), m7i-flex.large On-Demand, encrypted gp3 root30Gi·IMDSv2·maxPods35를 선언합니다.

nodeadm cloud-final drop-in과 MNG의 cluster NodeConfig를 병합합니다. custom LT에는 EKS cluster SG와 node SG를 포함하며 관리 root는 source SG를 추가합니다. 앱 root는 control plane target SG를 추가합니다. bridge API SG는 SSM bridge에서 오는443을 허용합니다.

CNI1.22.4(IRSA/노드 전 생성/prefix/network policy), CoreDNS1.13.2, kube-proxy1.35.3, PodIdentity1.3.10, EBSCSI1.66.0을 고정합니다. 자세한 eksbuild suffix는 main.tf에 있습니다. EBS/LBC는 정확한 kube-system SA Pod Identity role을 사용합니다. LBC policy는 공식 v3.5.0 [IAM policy](https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v3.5.0/docs/install/iam_policy.json)를 저장한 `terraform/config/aws-load-balancer-controller-policy.json`입니다.

운영자와 Argo deploy IAM principal의 cluster-admin Access Entry를 선언합니다. CI 생성 역할에는 자동 관리자 권한이 없으며 backend state는 root가 관리합니다. AWS 생성·quota·이미지 pull·nodeadm 런타임 동작은 mock 검사로 보장하지 않으며 [runbook](../../../docs/runbooks/eks-access.md)에서 확인합니다.
