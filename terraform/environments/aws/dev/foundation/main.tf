# 자원은 기능별 파일로 나눕니다. 자원과 그 IAM 권한은 같은 파일에 둡니다.
#   build.tf   소스 스냅샷 S3, CodeBuild, CodeBuild·Build Worker 역할
#   network.tf 공유 VPC, subnet, routing, 관리→앱 API 접근용 security group
#   observability.tf Loki 로그 저장 S3, Loki 역할(Pod Identity 연결은 management)
# TODO: deploy.tf(Deploy Worker 역할: ECR iris/services/* 태그(BatchGetImage·PutImage)만, 앱 EKS 권한 없음. ADR 0002)
