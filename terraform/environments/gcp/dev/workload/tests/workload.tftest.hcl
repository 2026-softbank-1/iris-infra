mock_provider "google" {
  mock_data "google_project" {
    defaults = { number = "123456789012" }
  }
}
variables {
  project_id             = "iris-fixture-project"
  management_oidc_issuer = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/EXAMPLE"
}
run "independent_private_workload" {
  command = plan
  assert {
    condition = toset(jsondecode(trimprefix(google_project_iam_member.argocd.condition[0].expression, "resource.name in "))) == toset([
      "projects/iris-fixture-project/zones/asia-northeast3-a/clusters/gcp-dev-workload",
      "projects/iris-fixture-project/locations/asia-northeast3-a/clusters/gcp-dev-workload",
      "projects/123456789012/zones/asia-northeast3-a/clusters/gcp-dev-workload",
      "projects/123456789012/locations/asia-northeast3-a/clusters/gcp-dev-workload"
    ]) && toset(google_project_iam_custom_role.connect.permissions) == toset(["container.clusters.get", "container.clusters.connect"])
    error_message = "Argo may connect only to this cluster via project ID or number."
  }
  assert {
    condition     = !contains(local.apis, "iam.googleapis.com") && !contains(local.apis, "iamcredentials.googleapis.com") && !contains(local.apis, "sts.googleapis.com")
    error_message = "Account owns federation APIs before workload CI authenticates."
  }
  assert {
    condition     = google_container_cluster.workload.datapath_provider == "ADVANCED_DATAPATH" && google_container_cluster.workload.private_cluster_config[0].enable_private_nodes && !google_container_cluster.workload.control_plane_endpoints_config[0].ip_endpoints_config[0].enabled
    error_message = "Private nodes and DNS-only management must remain enabled."
  }
  assert {
    condition     = google_container_node_pool.workload.autoscaling[0].min_node_count == 1 && google_container_node_pool.workload.autoscaling[0].max_node_count == 2 && google_container_node_pool.workload.node_config[0].machine_type == "e2-standard-2"
    error_message = "Keep the reviewed dev node budget."
  }
  assert {
    condition     = google_certificate_manager_certificate_map_entry.apps.hostname == "*.gcp.likelion.uk" && google_compute_router_nat.workload.source_subnetwork_ip_ranges_to_nat == "LIST_OF_SUBNETWORKS" && google_container_cluster.workload.deletion_protection
    error_message = "Independent certificate/egress and cluster protection are required."
  }
  assert {
    condition     = length(google_service_account_iam_member.argocd) == 2 && google_service_account_iam_member.ecr.member == "serviceAccount:iris-fixture-project.svc.id.goog[iris-system/ecr-credentials]"
    error_message = "Only reviewed management and credential identities may federate."
  }
}
