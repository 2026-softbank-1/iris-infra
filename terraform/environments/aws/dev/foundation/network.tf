locals {
  network_name = "${var.project}-${var.environment}"
  # Explicit ownership tags also form the CI IAM boundary for tags-on-create.
  network_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "Terraform"
    Component   = "network"
  }

  # Fixed slots keep CIDR allocations independent of AZ-name sorting.
  # Editing the AZ assigned to a slot still replaces its subnet; review the plan.
  network_slots = { for index, az in var.availability_zones : tostring(index) => az }
  nat_slots     = { for slot, az in local.network_slots : slot => az if var.nat_gateway_mode == "per_az" || slot == "0" }
  private_subnets = merge(
    { for slot, az in local.network_slots : "management-${slot}" => {
      az = az, slot = slot, cidr = cidrsubnet(var.vpc_cidr, 4, tonumber(slot)), cluster = var.management_cluster_name
    } },
    { for slot, az in local.network_slots : "workload-${slot}" => {
      az = az, slot = slot, cidr = cidrsubnet(var.vpc_cidr, 4, 2 + tonumber(slot)), cluster = var.workload_cluster_name
    } }
  )
}

resource "aws_vpc" "shared" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.network_tags, { Name = "${local.network_name}-vpc" })
}

resource "aws_internet_gateway" "shared" {
  vpc_id = aws_vpc.shared.id
  tags   = merge(local.network_tags, { Name = "${local.network_name}-igw" })
}

resource "aws_subnet" "public" {
  for_each = local.network_slots

  vpc_id                  = aws_vpc.shared.id
  availability_zone       = each.value
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 240 + tonumber(each.key))
  map_public_ip_on_launch = false
  tags = merge(local.network_tags, {
    Name                                                   = "${local.network_name}-public-${each.key}"
    "kubernetes.io/role/elb"                               = "1"
    "kubernetes.io/cluster/${var.management_cluster_name}" = "shared"
    "kubernetes.io/cluster/${var.workload_cluster_name}"   = "shared"
  })
}

resource "aws_subnet" "private" {
  for_each = local.private_subnets

  vpc_id                  = aws_vpc.shared.id
  availability_zone       = each.value.az
  cidr_block              = each.value.cidr
  map_public_ip_on_launch = false
  tags = merge(local.network_tags, {
    Name                                          = "${local.network_name}-${each.key}"
    "kubernetes.io/role/internal-elb"             = "1"
    "kubernetes.io/cluster/${each.value.cluster}" = "shared"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.shared.id
  tags   = merge(local.network_tags, { Name = "${local.network_name}-public" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.shared.id
}

resource "aws_route_table_association" "public" {
  for_each = local.network_slots

  subnet_id      = aws_subnet.public[each.key].id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  for_each = local.nat_slots

  domain = "vpc"
  tags   = merge(local.network_tags, { Name = "${local.network_name}-nat-${each.key}" })
}

resource "aws_nat_gateway" "egress" {
  for_each = local.nat_slots

  allocation_id     = aws_eip.nat[each.key].id
  subnet_id         = aws_subnet.public[each.key].id
  connectivity_type = "public"
  tags              = merge(local.network_tags, { Name = "${local.network_name}-nat-${each.key}" })

  depends_on = [aws_internet_gateway.shared]
}

resource "aws_route_table" "private" {
  for_each = local.private_subnets

  vpc_id = aws_vpc.shared.id
  tags   = merge(local.network_tags, { Name = "${local.network_name}-${each.key}" })
}

resource "aws_route" "private_internet" {
  for_each = local.private_subnets

  route_table_id         = aws_route_table.private[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.egress[var.nat_gateway_mode == "single" ? "0" : each.value.slot].id
}

resource "aws_route_table_association" "private" {
  for_each = local.private_subnets

  subnet_id      = aws_subnet.private[each.key].id
  route_table_id = aws_route_table.private[each.key].id
}

# Attach these additional groups in the future EKS stacks. They are not node
# groups, do not replace EKS-managed SGs, and do not expose application ingress.
resource "aws_security_group" "management_api_source" {
  name        = "${local.network_name}-management-api-source"
  description = "Management Worker source identity for the workload private EKS API"
  vpc_id      = aws_vpc.shared.id
  tags        = merge(local.network_tags, { Name = "${local.network_name}-management-api-source" })
}

resource "aws_security_group" "workload_api_target" {
  name        = "${local.network_name}-workload-api-target"
  description = "Additional workload control plane group accepting management HTTPS"
  vpc_id      = aws_vpc.shared.id
  tags        = merge(local.network_tags, { Name = "${local.network_name}-workload-api-target" })
}

resource "aws_vpc_security_group_egress_rule" "management_to_workload_api" {
  security_group_id            = aws_security_group.management_api_source.id
  referenced_security_group_id = aws_security_group.workload_api_target.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Management Worker to workload private Kubernetes API"
  tags                         = merge(local.network_tags, { Name = "${local.network_name}-management-to-workload-api" })
}

resource "aws_vpc_security_group_ingress_rule" "workload_api_from_management" {
  security_group_id            = aws_security_group.workload_api_target.id
  referenced_security_group_id = aws_security_group.management_api_source.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Workload private Kubernetes API from management Worker"
  tags                         = merge(local.network_tags, { Name = "${local.network_name}-workload-api-from-management" })
}
