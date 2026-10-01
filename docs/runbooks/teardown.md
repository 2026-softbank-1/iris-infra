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

## 네트워크 변경 복구

foundation에는 이미 빌드 입력 S3·CodeBuild·IAM이 함께 있으므로 네트워크 복구를 위해 전체
`terraform destroy`를 사용하지 않습니다. 빌드 입력 버킷은 `force_destroy=true`로 객체도 삭제될 수 있습니다.
실제 네트워크 적용 후 되돌릴 때는 network.tf의 제거·수정 계획과 후속 EKS/ALB/ENI 참조를 확인하고,
기존 빌드 주소와 state를 유지한 plan을 검토한 후 별도 승인으로 적용합니다. state를 삭제해 복구하지 않습니다.
생성된 NAT가 삭제되고 EIP가 해제됐는지 확인해 잔여 비용을 방지합니다. account와 bootstrap은 유지합니다.
