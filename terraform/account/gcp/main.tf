locals {
  apis            = toset(["serviceusage.googleapis.com", "cloudresourcemanager.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com"])
  workload_prefix = "gcp/dev/workload/"
  workflow_ref    = "2026-softbank-1/iris-infra/.github/workflows/gcp-terraform.yml@refs/heads/main"
  # projectIamAdmin is intentionally powerful: these grants are NOT an isolation
  # boundary against compromised CI. See README and the activation checklist.
  workload_roles = toset([
    "roles/compute.networkAdmin", "roles/compute.instanceAdmin.v1",
    "roles/container.admin", "roles/iam.serviceAccountAdmin", "roles/iam.serviceAccountUser",
    "roles/iam.workloadIdentityPoolAdmin", "roles/iam.roleAdmin",
    "roles/resourcemanager.projectIamAdmin", "roles/serviceusage.serviceUsageAdmin",
    "roles/certificatemanager.editor",
  ])
}
resource "google_project_service" "account" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
resource "google_service_account" "terraform" {
  account_id   = "iris-gcp-terraform"
  display_name = "GitHub main GCP workload Terraform"
  depends_on   = [google_project_service.account]
}
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "iris-github-ci"
  display_name              = "Iris GitHub workload CI"
  depends_on                = [google_project_service.account]
}
resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"          = "assertion.repository_id"
    "attribute.repository_id" = "assertion.repository_id"
  }
  attribute_condition = "assertion.repository_id == '${var.github_repository_id}' && assertion.repository_owner_id == '${var.github_owner_id}' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == '${local.workflow_ref}' && assertion.event_name in ['push', 'workflow_dispatch']"
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}
resource "google_service_account_iam_member" "github" {
  service_account_id = google_service_account.terraform.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}
resource "google_project_iam_member" "workload" {
  for_each = local.workload_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.terraform.email}"
}
resource "google_project_iam_custom_role" "secret_container" {
  role_id     = "irisTerraformSecretContainer"
  title       = "Iris secret container lifecycle (no versions)"
  permissions = ["secretmanager.secrets.create", "secretmanager.secrets.get", "secretmanager.secrets.update", "secretmanager.secrets.delete"]
  depends_on  = [google_project_service.account]
}
resource "google_project_iam_member" "secret_container" {
  project = var.project_id
  role    = google_project_iam_custom_role.secret_container.name
  member  = "serviceAccount:${google_service_account.terraform.email}"
}
resource "google_storage_bucket_iam_member" "metadata" {
  bucket = var.state_bucket_name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.terraform.email}"
}
resource "google_storage_bucket_iam_member" "workload_state" {
  bucket = var.state_bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.terraform.email}"
  condition {
    title       = "workload-state-only"
    description = "Direct object access only to workload state and its lock."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.state_bucket_name}/objects/${local.workload_prefix}')"
  }
}
