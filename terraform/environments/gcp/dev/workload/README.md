# GCP dev workload

독립 VPC·private node GKE Standard(서울 `asia-northeast3-a`)·Dataplane V2·
Workload Identity·Cloud NAT·Gateway API·global fixed IP·Certificate Manager를
소유합니다. GCS backend prefix는 `gcp/dev/workload`이며 AWS state를 읽지 않습니다.
dev node pool은 `e2-standard-2` 1–2대이고 단일 zone이므로 HA 보장은 없습니다.

`project_id`와 실제 management EKS `management_oidc_issuer`를 입력합니다.
Argo 연결용 외부 WIF와 Google SA, ECR 갱신기용 별도 Google SA를 만듭니다.
장기 SA key와 AWS key를 만들지 않습니다. 기존 AWS 계정의 ECR trust는 수동
`aws/dev/gcp-access` root에서 Google SA unique ID로 연결합니다.

Terraform은 Gateway의 LB/backend/health check를 선언하지 않습니다. Kubernetes
Gateway/HTTPRoute/HealthCheckPolicy를 보고 GKE가 생성하며 인증서·IP만 Terraform이
소유합니다. Sealed Secrets backup용 Secret Manager **container만** 만들며 개인키
version은 운영자가 별도로 백업합니다.

GKE API는 IAM 인증을 사용하는 DNS endpoint만 노출하고 IP endpoint는 비활성화합니다.
Argo의 IAM condition은 해당 zonal cluster의 zones/locations 이름만 허용합니다.
노드 metadata 접근은 사용자 namespace에서 차단하고 갱신기 SA만 허용합니다.
기본 Cloud Logging/Monitoring을 사용하고 AWS addon/storage/OTel은 복제하지 않습니다.

[GCP 런북](../../../../../docs/runbooks/gcp-workload.md)의 project·billing·권한·state
준비 후 `make tf-init/tf-plan/tf-apply STACK=gcp/dev/workload GCP_PROJECT_ID=...`로
수동 적용합니다. `deletion_protection=true`이며 폐기는 별도 승인 대상으로 둡니다.
