# teardown

현재 destroy 자동화는 제공하지 않습니다. 실제 자원 구현 후 아래 순서로 runbook을 확정합니다.

1. 타겟 AWS 계정·context와 보존할 배포 이력·DB backup을 확인합니다.
2. 앱 release·Ingress를 먼저 정리합니다.
3. ALB Controller가 생성한 ALB·target group·보안 그룹의 삭제를 확인합니다. 이 단계 전에 컨트롤러·클러스터를 삭제하지 않습니다.
4. 플랫폼·addon을 정리하고 잔여 PVC/PV/EBS 볼륨을 확인합니다.
5. workload / management를 각각 plan -destroy 검토 후 철거합니다.
6. 참조 자원이 제거된 뒤 foundation을 철거하고 NAT·ENI·ECR 보존 정책을 확인합니다.
7. account와 bootstrap은 일반 클러스터 철거 대상에서 제외합니다. state 버킷 삭제 보호를 유지합니다.

TODO: 스택별 destroy 승인·backup 보존·공유 자원 검사 절차를 구현에 맞춰 추가합니다.
