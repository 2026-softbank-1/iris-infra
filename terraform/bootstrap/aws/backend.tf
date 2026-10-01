# 최초에는 backend 선언 없이 로컬 state로 S3를 생성합니다.
# 버킷 생성 후 아래 선언을 활성화하고 init -migrate-state로 이전합니다.
terraform {
  backend "s3" {}
}
