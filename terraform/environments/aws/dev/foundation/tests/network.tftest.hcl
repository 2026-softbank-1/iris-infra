# Mock providers cannot serve the imported zone, and ACM validation options are
# computed by AWS; fixed values keep plan-time indexing deterministic.
override_resource {
  target = aws_route53_zone.main
  values = { zone_id = "Z0123456789ABCDEFGHIJ", name_servers = ["ns-1.awsdns-01.org"] }
}
override_resource {
  target = aws_acm_certificate.wildcard
  values = { domain_validation_options = [
    { domain_name = "*.likelion.uk", resource_record_name = "_x.likelion.uk.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." },
    { domain_name = "likelion.uk", resource_record_name = "_x.likelion.uk.", resource_record_type = "CNAME", resource_record_value = "_y.acm-validations.aws." }
  ] }
}
override_resource {
  target = aws_acm_certificate.internal
  values = { domain_validation_options = [
    { domain_name = "*.internal.likelion.uk", resource_record_name = "_z.internal.likelion.uk.", resource_record_type = "CNAME", resource_record_value = "_w.acm-validations.aws." }
  ] }
}
# Mock apply resolves computed IDs without credentials or AWS API calls.
mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-build-role" }
  }
}

variables {
  aws_account_id = "123456789012"
}

run "default_network" {
  command = apply

  assert {
    condition = (
      alltrue([for name, value in {
        vpc_cidr                = var.vpc_cidr, nat_gateway_mode = var.nat_gateway_mode,
        management_cluster_name = var.management_cluster_name, workload_cluster_name = var.workload_cluster_name,
        } : regex("(?m)^${name}\\s*=\\s*\"([^\"]+)\"", file("${path.module}/terraform.tfvars.example"))[0] == value
      ]) &&
      tolist(jsondecode(regex("(?m)^availability_zones\\s*=\\s*(\\[[^\\n]+\\])", file("${path.module}/terraform.tfvars.example"))[0])) == var.availability_zones
    )
    error_message = "The checked-in example must match the network defaults used by CI."
  }

  assert {
    condition = (
      aws_vpc.shared.cidr_block == "10.40.0.0/16" &&
      aws_vpc.shared.enable_dns_support && aws_vpc.shared.enable_dns_hostnames &&
      length(aws_subnet.public) == 2 && length(aws_subnet.private) == 4
    )
    error_message = "CI defaults must provision one DNS-enabled VPC and six subnets across two AZs."
  }

  assert {
    condition = (
      toset([for subnet in aws_subnet.public : subnet.cidr_block]) == toset(["10.40.240.0/24", "10.40.241.0/24"]) &&
      toset([for subnet in aws_subnet.private : subnet.cidr_block]) == toset([
        "10.40.0.0/20", "10.40.16.0/20", "10.40.32.0/20", "10.40.48.0/20",
      ]) &&
      alltrue([for subnet in concat(values(aws_subnet.public), values(aws_subnet.private)) :
        subnet.vpc_id == aws_vpc.shared.id && !subnet.map_public_ip_on_launch
      ])
    )
    error_message = "The reserved subnet ranges must be disjoint, in the shared VPC, with public IP assignment disabled."
  }

  assert {
    condition = (
      aws_internet_gateway.shared.vpc_id == aws_vpc.shared.id &&
      aws_route.public_internet.gateway_id == aws_internet_gateway.shared.id &&
      aws_route.public_internet.route_table_id == aws_route_table.public.id &&
      aws_route.public_internet.destination_cidr_block == "0.0.0.0/0" &&
      alltrue([for slot, association in aws_route_table_association.public :
        association.subnet_id == aws_subnet.public[slot].id && association.route_table_id == aws_route_table.public.id
      ])
    )
    error_message = "Both public subnets must use the route table with the Internet Gateway default route."
  }

  assert {
    condition = (
      length(aws_nat_gateway.egress) == 2 && length(aws_eip.nat) == 2 &&
      aws_nat_gateway.egress["0"].subnet_id == aws_subnet.public["0"].id &&
      aws_nat_gateway.egress["0"].allocation_id == aws_eip.nat["0"].id &&
      aws_nat_gateway.egress["0"].connectivity_type == "public" && aws_eip.nat["0"].domain == "vpc" &&
      alltrue([for key, route in aws_route.private_internet :
        route.nat_gateway_id == aws_nat_gateway.egress[local.private_subnets[key].slot].id &&
        route.destination_cidr_block == "0.0.0.0/0" && route.route_table_id == aws_route_table.private[key].id &&
        aws_route_table.private[key].vpc_id == aws_vpc.shared.id &&
        aws_route_table_association.private[key].subnet_id == aws_subnet.private[key].id &&
        aws_route_table_association.private[key].route_table_id == aws_route_table.private[key].id
      ])
    )
    error_message = "Default per-AZ NAT routes must remain in the same AZ, never directly through the IGW."
  }

  assert {
    condition = (
      alltrue([for subnet in aws_subnet.public :
        subnet.tags["kubernetes.io/role/elb"] == "1" &&
        subnet.tags["kubernetes.io/cluster/iris-dev-management"] == "shared" &&
        subnet.tags["kubernetes.io/cluster/iris-dev-workload"] == "shared"
      ]) &&
      alltrue([for key, subnet in aws_subnet.private :
        subnet.tags["kubernetes.io/role/internal-elb"] == "1" &&
        subnet.tags["kubernetes.io/cluster/${startswith(key, "management-") ? "iris-dev-management" : "iris-dev-workload"}"] == "shared" &&
        !contains(keys(subnet.tags), "kubernetes.io/cluster/${startswith(key, "management-") ? "iris-dev-workload" : "iris-dev-management"}")
      ]) &&
      alltrue([for subnet in concat(values(aws_subnet.public), values(aws_subnet.private)) :
        subnet.tags["Project"] == "iris" && subnet.tags["Environment"] == "dev" &&
        subnet.tags["ManagedBy"] == "Terraform" && subnet.tags["Component"] == "network"
      ])
    )
    error_message = "LB discovery and ownership tags must distinguish cluster-private subnets while sharing public subnets."
  }

  assert {
    condition = (
      aws_security_group.management_api_source.vpc_id == aws_vpc.shared.id &&
      aws_security_group.workload_api_target.vpc_id == aws_vpc.shared.id &&
      aws_vpc_security_group_ingress_rule.workload_api_from_management.security_group_id == aws_security_group.workload_api_target.id &&
      aws_vpc_security_group_ingress_rule.workload_api_from_management.referenced_security_group_id == aws_security_group.management_api_source.id &&
      aws_vpc_security_group_egress_rule.management_to_workload_api.security_group_id == aws_security_group.management_api_source.id &&
      aws_vpc_security_group_egress_rule.management_to_workload_api.referenced_security_group_id == aws_security_group.workload_api_target.id &&
      alltrue([
        for rule in [aws_vpc_security_group_ingress_rule.workload_api_from_management, aws_vpc_security_group_egress_rule.management_to_workload_api] :
        rule.ip_protocol == "tcp" && rule.from_port == 443 && rule.to_port == 443 &&
        rule.cidr_ipv4 == null && rule.cidr_ipv6 == null && rule.prefix_list_id == null
      ])
    )
    error_message = "The API path must be SG-to-SG TCP 443, with no CIDR-based ingress or egress."
  }

  assert {
    condition = (
      output.vpc_id == aws_vpc.shared.id &&
      output.management_cluster_name == "iris-dev-management" && output.workload_cluster_name == "iris-dev-workload" &&
      output.management_api_source_security_group_id == aws_security_group.management_api_source.id &&
      output.workload_api_target_security_group_id == aws_security_group.workload_api_target.id &&
      alltrue([for slot, az in var.availability_zones :
        output.public_subnet_ids_by_az[az] == aws_subnet.public[tostring(slot)].id &&
        output.management_subnet_ids_by_az[az] == aws_subnet.private["management-${slot}"].id &&
        output.workload_subnet_ids_by_az[az] == aws_subnet.private["workload-${slot}"].id &&
        aws_subnet.public[tostring(slot)].availability_zone == az &&
        aws_subnet.private["management-${slot}"].availability_zone == az &&
        aws_subnet.private["workload-${slot}"].availability_zone == az
      ])
    )
    error_message = "The output contract must map every subnet to its AZ and expose the correct additional SGs and cluster names."
  }
}

run "nat_single" {
  command = apply
  variables { nat_gateway_mode = "single" }

  assert {
    condition = (
      length(aws_nat_gateway.egress) == 1 && length(aws_eip.nat) == 1 &&
      alltrue([for slot, nat in aws_nat_gateway.egress :
        nat.subnet_id == aws_subnet.public[slot].id && nat.allocation_id == aws_eip.nat[slot].id
      ]) &&
      alltrue([for key, config in local.private_subnets :
        aws_route.private_internet[key].nat_gateway_id == aws_nat_gateway.egress["0"].id
      ])
    )
    error_message = "Single NAT mode must use slot 0 for both AZs."
  }
}

run "custom_network_inputs" {
  command = apply
  variables {
    aws_region              = "us-west-2"
    vpc_cidr                = "10.80.0.0/16"
    availability_zones      = ["us-west-2c", "us-west-2a"]
    management_cluster_name = "other-management"
    workload_cluster_name   = "other-workload"
  }

  assert {
    condition = (
      aws_subnet.public["0"].availability_zone == "us-west-2c" &&
      aws_subnet.public["0"].cidr_block == "10.80.240.0/24" &&
      aws_subnet.private["management-0"].cidr_block == "10.80.0.0/20" &&
      aws_subnet.private["workload-1"].cidr_block == "10.80.48.0/20" &&
      output.management_cluster_name == "other-management" && output.workload_cluster_name == "other-workload" &&
      aws_subnet.public["0"].tags["kubernetes.io/cluster/other-management"] == "shared" &&
      aws_subnet.private["workload-1"].tags["kubernetes.io/cluster/other-workload"] == "shared"
    )
    error_message = "CIDR slots must stay stable with reversed AZ names and custom inputs must reach discovery tags and outputs."
  }
}

run "reject_noncanonical_cidr" {
  command = plan
  variables { vpc_cidr = "10.40.1.0/16" }
  expect_failures = [var.vpc_cidr]
}

run "reject_wrong_prefix" {
  command = plan
  variables { vpc_cidr = "10.40.0.0/24" }
  expect_failures = [var.vpc_cidr]
}

run "reject_ipv6" {
  command = plan
  variables { vpc_cidr = "fd00::/16" }
  expect_failures = [var.vpc_cidr]
}

run "reject_duplicate_azs" {
  command = plan
  variables { availability_zones = ["ap-northeast-2a", "ap-northeast-2a"] }
  expect_failures = [var.availability_zones]
}

run "reject_single_az" {
  command = plan
  variables { availability_zones = ["ap-northeast-2a"] }
  expect_failures = [var.availability_zones]
}

run "reject_other_region_az" {
  command = plan
  variables { availability_zones = ["us-west-2a", "us-west-2c"] }
  expect_failures = [var.availability_zones]
}

run "reject_nat_mode" {
  command = plan
  variables { nat_gateway_mode = "none" }
  expect_failures = [var.nat_gateway_mode]
}

run "reject_same_cluster_names" {
  command = plan
  variables { workload_cluster_name = "iris-dev-management" }
  expect_failures = [var.workload_cluster_name]
}
