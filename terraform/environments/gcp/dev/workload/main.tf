locals {
  name   = "gcp-dev-workload"
  region = "asia-northeast3"
  zone   = "asia-northeast3-a"
  apis   = toset(["compute.googleapis.com", "container.googleapis.com", "certificatemanager.googleapis.com", "secretmanager.googleapis.com", "logging.googleapis.com", "monitoring.googleapis.com"])
  cluster_iam_resource_names = flatten([
    for project in [var.project_id, data.google_project.current.number] : [
      "projects/${project}/zones/${local.zone}/clusters/${local.name}",
      "projects/${project}/locations/${local.zone}/clusters/${local.name}"
    ]
  ])
}
data "google_project" "current" {
  project_id = var.project_id
}
resource "google_project_service" "required" {
  for_each           = local.apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
resource "google_compute_network" "workload" {
  name                    = local.name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}
resource "google_compute_subnetwork" "workload" {
  name                     = local.name
  region                   = local.region
  network                  = google_compute_network.workload.id
  ip_cidr_range            = var.vpc_cidr
  private_ip_google_access = true
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pod_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.service_cidr
  }
}
resource "google_compute_router" "workload" {
  name    = local.name
  region  = local.region
  network = google_compute_network.workload.id
}
resource "google_compute_router_nat" "workload" {
  name                               = local.name
  router                             = google_compute_router.workload.name
  region                             = local.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = google_compute_subnetwork.workload.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}
resource "google_service_account" "node" {
  account_id = "iris-gcp-node"
  depends_on = [google_project_service.required]
}
resource "google_project_iam_member" "node" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.node.email}"
}
resource "google_container_cluster" "workload" {
  name                     = local.name
  location                 = local.zone
  network                  = google_compute_network.workload.id
  subnetwork               = google_compute_subnetwork.workload.id
  deletion_protection      = true
  remove_default_node_pool = true
  initial_node_count       = 1
  networking_mode          = "VPC_NATIVE"
  datapath_provider        = "ADVANCED_DATAPATH"
  release_channel { channel = "REGULAR" }
  gateway_api_config { channel = "CHANNEL_STANDARD" }
  private_cluster_config { enable_private_nodes = true }
  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic    = true
      enable_k8s_tokens_via_dns = false
      enable_k8s_certs_via_dns  = false
    }
    ip_endpoints_config { enabled = false }
  }
  workload_identity_config { workload_pool = "${var.project_id}.svc.id.goog" }
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  logging_config { enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"] }
  monitoring_config { enable_components = ["SYSTEM_COMPONENTS"] }
  depends_on = [google_project_service.required, google_project_iam_member.node]
}
resource "google_container_node_pool" "workload" {
  name               = "workload"
  location           = local.zone
  cluster            = google_container_cluster.workload.name
  initial_node_count = 1
  autoscaling {
    min_node_count = 1
    max_node_count = 2
  }
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  node_config {
    machine_type    = var.node_machine_type
    service_account = google_service_account.node.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    disk_size_gb    = 30
    disk_type       = "pd-balanced"
    image_type      = "COS_CONTAINERD"
    workload_metadata_config { mode = "GKE_METADATA" }
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}
resource "google_compute_global_address" "apps" {
  name       = "iris-gcp-apps"
  depends_on = [google_project_service.required]
}
resource "google_certificate_manager_dns_authorization" "apps" {
  name       = "iris-gcp-apps"
  domain     = var.base_domain
  depends_on = [google_project_service.required]
}
resource "google_certificate_manager_certificate" "apps" {
  name = "iris-gcp-apps"
  managed {
    domains            = ["*.${var.base_domain}"]
    dns_authorizations = [google_certificate_manager_dns_authorization.apps.id]
  }
}
resource "google_certificate_manager_certificate_map" "apps" {
  name       = "iris-gcp-apps"
  depends_on = [google_project_service.required]
}
resource "google_certificate_manager_certificate_map_entry" "apps" {
  name         = "iris-gcp-apps"
  map          = google_certificate_manager_certificate_map.apps.name
  certificates = [google_certificate_manager_certificate.apps.id]
  hostname     = "*.${var.base_domain}"
}
resource "google_service_account" "argocd" {
  account_id = "iris-gcp-argocd"
  depends_on = [google_project_service.required]
}
resource "google_iam_workload_identity_pool" "argocd" {
  workload_identity_pool_id = "iris-argocd"
  depends_on                = [google_project_service.required]
}
resource "google_iam_workload_identity_pool_provider" "argocd" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.argocd.workload_identity_pool_id
  workload_identity_pool_provider_id = "management-eks"
  attribute_mapping                  = { "google.subject" = "assertion.sub" }
  attribute_condition                = "assertion.sub in ['system:serviceaccount:argocd:argocd-application-controller', 'system:serviceaccount:argocd:argocd-server']"
  oidc { issuer_uri = var.management_oidc_issuer }
}
resource "google_service_account_iam_member" "argocd" {
  for_each           = toset(["argocd-application-controller", "argocd-server"])
  service_account_id = google_service_account.argocd.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.argocd.name}/subject/system:serviceaccount:argocd:${each.value}"
}
resource "google_project_iam_custom_role" "connect" {
  depends_on  = [google_project_service.required]
  role_id     = "irisGkeConnect"
  title       = "Iris GKE connection"
  permissions = ["container.clusters.get", "container.clusters.connect"]
}
resource "google_project_iam_member" "argocd" {
  project = var.project_id
  role    = google_project_iam_custom_role.connect.name
  member  = "serviceAccount:${google_service_account.argocd.email}"
  condition {
    title = "workload-only"
    # The DNS endpoint uses the project number; both aliases identify this cluster.
    expression = "resource.name in ${jsonencode(local.cluster_iam_resource_names)}"
  }
  lifecycle {
    create_before_destroy = true
  }
}
resource "google_service_account" "ecr" {
  account_id = "iris-gcp-ecr"
  depends_on = [google_project_service.required]
}
resource "google_service_account_iam_member" "ecr" {
  service_account_id = google_service_account.ecr.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[iris-system/ecr-credentials]"
  depends_on         = [google_container_cluster.workload]
}
resource "google_secret_manager_secret" "sealed_keys" {
  secret_id = "iris-gcp-sealed-secrets-key"
  replication {
    auto {}
  }
  deletion_protection = true
  depends_on          = [google_project_service.required]
}
