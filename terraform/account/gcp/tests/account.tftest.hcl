mock_provider "google" {}
variables {
  project_id           = "iris-fixture-project"
  state_bucket_name    = "iris-fixture-state"
  github_repository_id = "123456789"
  github_owner_id      = "987654321"
}
run "restricted_workload_identity" {
  command = plan
  assert {
    condition     = google_iam_workload_identity_pool_provider.github.attribute_condition == "assertion.repository_id == '123456789' && assertion.repository_owner_id == '987654321' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == '2026-softbank-1/iris-infra/.github/workflows/gcp-terraform.yml@refs/heads/main' && assertion.event_name in ['push', 'workflow_dispatch']" && google_iam_workload_identity_pool_provider.github.attribute_mapping["google.subject"] == "assertion.repository_id"
    error_message = "CI must bind numeric IDs, exact main workflow and allowed events."
  }
  assert {
    condition     = google_storage_bucket_iam_member.workload_state.condition[0].expression == "resource.name.startsWith('projects/_/buckets/iris-fixture-state/objects/gcp/dev/workload/')" && google_storage_bucket_iam_member.metadata.role == "roles/storage.legacyBucketReader"
    error_message = "Direct object access must exclude account/bootstrap state."
  }
  assert {
    condition     = !contains(local.workload_roles, "roles/owner") && contains(local.workload_roles, "roles/editor") && contains(local.workload_roles, "roles/resourcemanager.projectIamAdmin") && !contains(google_project_iam_custom_role.secret_container.permissions, "secretmanager.versions.access")
    error_message = "CI must retain project Editor and IAM administration; the custom secret role remains container-only."
  }
  assert {
    condition     = local.apis == toset(["serviceusage.googleapis.com", "cloudresourcemanager.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com"]) && alltrue([for api in google_project_service.account : !api.disable_on_destroy])
    error_message = "Account APIs must have exactly one protected owner."
  }
}
