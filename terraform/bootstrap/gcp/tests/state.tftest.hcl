mock_provider "google" {}
variables {
  project_id        = "iris-fixture-project"
  state_bucket_name = "iris-fixture-tfstate"
}
run "protected_state" {
  command = plan
  assert {
    condition     = google_storage_bucket.state.versioning[0].enabled && google_storage_bucket.state.public_access_prevention == "enforced" && !google_storage_bucket.state.force_destroy
    error_message = "State must be versioned, private, and retained."
  }
}
