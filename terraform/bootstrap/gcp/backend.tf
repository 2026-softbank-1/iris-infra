# First apply uses local state. After creating the bucket, explicitly migrate it
# with: terraform init -migrate-state -backend-config=backend.hcl
terraform {
  backend "gcs" {}
}
