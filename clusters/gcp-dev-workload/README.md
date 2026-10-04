# gcp-dev-workload

서울 GKE Standard, 별도 GCS state, 독립 HTTPS Gateway를 사용하는 dev target입니다.
Argo CD/ECR은 AWS management 영역을 공유하고 사용자 요청은 AWS 프록시를 거치지 않습니다.
addon은 `helm/gitops`의 `gcp.enabled` opt-in으로 활성화됩니다.
`values/sealed-secrets.yaml`은 이 클러스터 전용 controller이며 AWS key를 복사하지 않습니다.

[배포·검증·복구 런북](../../docs/runbooks/gcp-workload.md),
[handoff 계약](../../contracts/gcp-target.md)을 참고합니다.
