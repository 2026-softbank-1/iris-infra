# 개발 EKS 철거와 복구

시간 기반 자동 철거는 없습니다. 일요일의 실제 종료 시각에 운영자가 계정·target·보존 데이터·삭제 plan을 확인하고 수행합니다. **foundation 전체 destroy, state 버킷/기존 build/ECR 삭제는 사용하지 않습니다.** 코드 revert나 workflow 실패도 앞서 생성한 자원을 자동으로 되돌리지 않습니다.

1. 두 API 터널과 정확한 operator identity를 확인하고 필요한 Prometheus/Grafana/DB 자료를 외부에 보관합니다. gp3 StorageClass는 Delete이므로 PVC 삭제 시 데이터가 사라집니다.
2. 앱/Ingress를 먼저 삭제하고 LBC가 만든 ALB/target group/SG가 정리됐는지 확인합니다. 아직 ALB가 없으면 이 단계를 건너뜁니다. LBC나 클러스터를 먼저 지우지 않습니다.
3. Argo root의 자동 sync를 중지하거나 root를 non-cascading 삭제하여 재생성을 막습니다. 각 addon Application과 소유 자원을 검토하여 삭제합니다. automated prune=false이므로 Application 객체만 삭제하는 것으로 addon 자원이 지워진다고 가정하지 않습니다. monitoring PVC/PV와 EBS 실제 삭제 여부를 확인합니다. CRD/finalizer를 강제로 제거하기 전에 인스턴스가 남았는지 봅니다.
4. Argo·관측 addon을 정리한 뒤 workload/management 각각 `terraform plan -destroy -out=<로컬-저장-plan>`을 검토하고 같은 plan을 apply합니다. 이 두 root만 철거합니다. AWS 계정·state key를 재확인하고 foundation/bootstrap/account를 포함하지 않습니다. `.scaffold` 제거 이후 main에 코드가 남아 있으면 다음 merge가 EKS를 재생성하므로 철거 변경의 CI 상태도 함께 계획합니다.
5. 클러스터/ALB/ENI 참조 제거 후 foundation의 **네트워크/access/Argo IAM만 제거하는 별도 변경**을 준비합니다. build.tf/ecr-platform.tf와 기존 build/ECR 주소는 보존합니다. NAT/EIP/route/IGW/subnet/VPC/bridge/공통 Argo 역할 출력 및 후속 소비자를 일관되게 제거하고 실제 plan을 검토합니다. 네트워크만 줄이려면 per_az→single도 검토할 수 있지만 NAT0 비용은 계속 남습니다.
6. foundation CI guard는 NAT0/EIP0/VPC/subnet 삭제를 의도적으로 차단합니다. 이를 자동 우회하지 않으며 철거용 수동 저장 plan에서 **build/ECR 삭제·교체가 0개인지** 확인한 뒤 별도 승인으로 적용합니다. 일반 CI 보호는 유지합니다. state를 삭제하거나 광범위 `-target` destroy로 우회하지 않습니다.
7. NAT gateway 삭제 완료·EIP 해제·bridge/잔여 ENI·EBS volume/snapshot·log 잔여 비용을 확인합니다. account와 bootstrap 및 플랫폼 ECR/tagged image는 보존합니다. ECR 정리는 별도 lifecycle/보존 정책으로 처리합니다.

부분 실패 시 실제 state와 AWS 상태를 확인하고 재계획합니다. 권한 오류/소유권 tag 불일치/보호 자원 교체가 있으면 영향을 받는 stack을 멈춥니다. 실패한 smoke exercise는 finally cleanup을 시도하며 남은 정확한 namespace/Pod와 node uncordon만 수동 정리합니다. build 입력 버킷은 기존 force_destroy=true이므로 전체 foundation destroy는 객체까지 삭제할 수 있습니다.

## 비용 범위

72–96시간, EKS2개·m7i-flex.large4대·NAT2개·NAT 공인IPv4 2개·bridge1대·node root EBS120Gi·관측 EBS54Gi 기준 고정 비용은 계획상 약 **$60–80**입니다. 보장/상한이 아니며 실제 서울 단가·가동시간·Free credit 적용을 확인합니다. NAT 처리량·AZ/인터넷 전송·CloudWatch/ECR/S3·CodeBuild·추가 PVC/스냅샷·세금/환율·후속 ALB는 별도입니다. 크레딧을 쓰더라도 소비는 발생합니다.

[EKS 가격](https://aws.amazon.com/eks/pricing/), [EC2 가격](https://aws.amazon.com/ec2/pricing/on-demand/), [VPC/NAT/IPv4 가격](https://aws.amazon.com/vpc/pricing/), [EBS 가격](https://aws.amazon.com/ebs/pricing/)과 Billing 실제 사용량을 확인합니다. EKS standard support를 유지하며 extended support 비용으로 자동 넘어가는 구성은 사용하지 않습니다.
