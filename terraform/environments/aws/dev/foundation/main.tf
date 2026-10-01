# 자원은 기능별 파일로 나눕니다. 자원과 그 IAM 권한은 같은 파일에 둡니다.
#   build.tf   소스 스냅샷 S3, CodeBuild, CodeBuild·Build Worker 역할
# TODO: network.tf(VPC, subnet, routing, 공통 접근용 security group),
#       deploy.tf(helm/iris-service OCI 저장소, Deployer Worker 역할)
