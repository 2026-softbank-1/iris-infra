# 0003: Private EKS와 addon GitOps

상태: 계획 v4 리뷰 READY와 사용자 구현 승인을 받은 결정. 배포 승인은 별도.

기존 ECR/VPC/build를 보존하면서 일요일까지 3–4일 개발 환경을 가동하려는 요구를 따른다. 운영자 접근은 SSM 관리 EC2 + private API, 앱 노드는 AZ별 1대씩 두기로 한다.

EKS 1.35 standard support, AL2023 x86_64 release 1.35.8-20260930, m7i-flex.large On-Demand, 각 클러스터 노드 2대, maxPods35/prefix delegation을 고정한다. AZ별 NAT가 기본이며 single 선택을 유지한다. public API와 기본 ALB/Ingress는 만들지 않는다.

Argo는 management에만 설치하여 두 클러스터의 baseline/LBC/metrics/monitoring을 관리한다. Terraform managed addon과 Helm/Argo 소유권을 분리하고 사용자 앱은 ADR 0002의 GitOps 배포 결정을 유지하고 별도 ApplicationSet으로 연결한다. 이번 bootstrap은 공통 addon만 생성한다. CNI의 IRSA는 노드 생성 전에 준비하고 EBS/LBC/Argo는 Pod Identity를 사용한다. Argo의 management 자기 assume 및 두 deploy 역할, 3개 SA, Access Entry를 일치시킨다. 개발 bootstrap에서는 deploy/operator에 cluster-admin을 부여하며 후속 축소가 필요하다.

Chart/image/AMI/addon 버전과 검토 Git SHA를 고정한다. private Git 읽기 자격 증명은 운영자가 외부에서 제공한다. Argo bootstrap과 실제 Kubernetes 검증은 CI Terraform apply와 별도로 실행한다. main merge는 인프라의 자동 apply 승인 경계이며 agent가 merge하지 않는다.

관측 PVC는 gp3 encrypted/WFFC/Delete, Prometheus20Gi/3d/15GB·Grafana5Gi·Alertmanager2Gi이다. singleton EBS 서비스는 AZ 장애 시 즉시 복구되는 HA가 아니다. 2노드 failover는 stateless 샘플로 opt-in drain 검사한다.

Free 계정의 EC2 eligibility와 EKS 생성 가능 여부는 별개다. Paid 전환을 자동으로 하지 않으며 quota/서비스 허용/크레딧을 배포 전 확인한다. 72–96시간 고정 비용은 약 $60–80의 계획 추정값이고 NAT 처리량·전송·로그·빌드·ECR·세금/환율은 별도다. 실제 종료 시각을 운영자가 정하고 선택 철거하며 build/ECR/state를 보존한다.

검증은 backend 없는 validate, 격리 mock, fake 명령, chart/schema/digest 렌더 검사와 실제 배포 후 API/TLS/RBAC/CNI/PodIdentity/PVC/metrics/network/failover 검사를 구분한다. 실제 IAM 충분성·EKS 생성·노드 용량·런타임 호환은 정적 검사로 보장하지 않는다.
