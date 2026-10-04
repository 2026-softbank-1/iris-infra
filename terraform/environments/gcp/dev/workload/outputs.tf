output "target" {
  description = "GCP infrastructure metadata only; no tokens, keys or state."
  value = {
    schema_version         = 1
    id                     = "gcp-dev-workload"
    project_id             = var.project_id
    location               = local.zone
    region                 = local.region
    name                   = google_container_cluster.workload.name
    endpoint               = "https://${google_container_cluster.workload.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint}"
    kube_context           = "iris-gcp-dev-workload"
    network                = google_compute_network.workload.name
    subnet                 = google_compute_subnetwork.workload.name
    denied_cidrs           = [var.vpc_cidr, var.pod_cidr, var.service_cidr, "169.254.0.0/16"]
    base_domain            = var.base_domain
    ip_address             = google_compute_global_address.apps.address
    address_name           = google_compute_global_address.apps.name
    certificate_map        = google_certificate_manager_certificate_map.apps.name
    certificate_name       = google_certificate_manager_certificate.apps.name
    dns_authorization      = google_certificate_manager_dns_authorization.apps.dns_resource_record
    argocd_service_account = google_service_account.argocd.email
    wif_provider           = google_iam_workload_identity_pool_provider.argocd.name
    ecr_service_account    = google_service_account.ecr.email
    ecr_service_account_id = google_service_account.ecr.unique_id
    ecr_audience           = var.ecr_audience
    sealed_key_secret      = google_secret_manager_secret.sealed_keys.secret_id
  }
}
