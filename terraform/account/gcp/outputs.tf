output "ci" {
  value = {
    project_id                 = var.project_id
    project_number             = split("/", google_iam_workload_identity_pool.github.name)[1]
    state_bucket               = var.state_bucket_name
    state_prefix               = "gcp/dev/workload"
    service_account            = google_service_account.terraform.email
    workload_identity_provider = google_iam_workload_identity_pool_provider.github.name
    github_repository_id       = var.github_repository_id
    github_owner_id            = var.github_owner_id
    region                     = "asia-northeast3"
    zone                       = "asia-northeast3-a"
  }
}
