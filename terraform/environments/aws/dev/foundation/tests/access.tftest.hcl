mock_provider "aws" {
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" } }
}
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
variables { aws_account_id = "123456789012" }
run "private_bridge_and_argocd" {
  command = apply
  assert {
    condition     = !aws_instance.ssm_bridge.associate_public_ip_address && aws_instance.ssm_bridge.instance_type == "t3.micro" && aws_instance.ssm_bridge.subnet_id == aws_subnet.private["management-0"].id && length(aws_security_group.ssm_bridge.ingress) == 0 && aws_instance.ssm_bridge.credit_specification[0].cpu_credits == "standard"
    error_message = "Bridge must be private, no inbound/SSH and no surplus CPU charges."
  }
  assert {
    condition     = aws_instance.ssm_bridge.volume_tags == tomap(local.access_tags) && aws_instance.ssm_bridge.volume_tags["Component"] == "access" && aws_instance.ssm_bridge.root_block_device[0].encrypted && aws_instance.ssm_bridge.root_block_device[0].volume_type == "gp3" && aws_instance.ssm_bridge.root_block_device[0].volume_size == 8 && aws_instance.ssm_bridge.root_block_device[0].delete_on_termination
    error_message = "Bridge must send all owner tags in the launch request and retain its encrypted 8Gi gp3 root volume. Post-launch root_block_device tags cannot satisfy RunInstances RequestTag conditions."
  }
  assert {
    condition     = aws_iam_role_policy_attachment.ssm_bridge.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" && length(aws_iam_role.argocd_deploy) == 2 && jsondecode(aws_iam_role_policy.argocd_management.policy).Statement[0].Action == ["sts:AssumeRole", "sts:TagSession"]
    error_message = "Bridge gets no Kubernetes admin role; GitOps roles only assume named cluster identities."
  }
}

run "deploy_worker_boundary" {
  command = apply
  assert {
    condition = aws_iam_role.deploy_worker.name == "${var.project}-${var.environment}-deploy-worker" && jsonencode(jsondecode(aws_iam_role.deploy_worker.assume_role_policy).Statement) == jsonencode([{
      Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" },
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name,
        "aws:RequestTag/kubernetes-namespace"       = "iris-platform",
        "aws:RequestTag/kubernetes-service-account" = "deploy-worker"
      } }
    }])
    error_message = "Only management iris-platform/deploy-worker may assume the Deploy Worker role."
  }
  assert {
    condition = jsonencode(jsondecode(aws_iam_role_policy.deploy_worker.policy).Statement) == jsonencode([{
      Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:PutImage"], Resource = "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.project}/services/*"
    }]) && output.deploy_worker_role_arn == aws_iam_role.deploy_worker.arn
    error_message = "Deploy Worker may only read manifests and tag images in user service repositories, without Kubernetes/CodeBuild/layer push access."
  }
}

run "control_api_boundary" {
  command = apply
  assert {
    condition = aws_iam_role.control_api.name == "${var.project}-${var.environment}-control-api" && jsonencode(jsondecode(aws_iam_role.control_api.assume_role_policy).Statement) == jsonencode([{
      Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { Service = "pods.eks.amazonaws.com" },
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name,
        "aws:RequestTag/kubernetes-namespace"       = "iris-platform",
        "aws:RequestTag/kubernetes-service-account" = "iris-platform-api"
      } }
    }])
    error_message = "Only management iris-platform/iris-platform-api may assume the Control API role."
  }
  assert {
    condition = aws_iam_role_policy.control_api.name == "read-build-logs" && jsonencode(jsondecode(aws_iam_role_policy.control_api.policy).Statement) == jsonencode([{
      Sid = "ReadBuildLogs", Effect = "Allow", Action = ["logs:GetLogEvents"], Resource = "${aws_cloudwatch_log_group.build.arn}:*"
    }]) && output.control_api_role_arn == aws_iam_role.control_api.arn
    error_message = "The existing read-build-logs policy must stay unchanged: only build log events of the CodeBuild log group."
  }
  assert {
    condition = aws_iam_role_policy.control_api_source_uploads.name == "source-uploads" && aws_iam_role_policy.control_api_source_uploads.role == aws_iam_role.control_api.id && jsonencode(jsondecode(aws_iam_role_policy.control_api_source_uploads.policy).Statement) == jsonencode([
      { Sid = "WriteSourceUploads", Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = "${aws_s3_bucket.build_artifacts.arn}/uploads/*" },
      { Sid = "ReadSnapshotsForDiagnosis", Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.build_artifacts.arn}/snapshots/*" }
    ])
    error_message = "Control API may only write uploads/* (PutObject, AbortMultipartUpload) and read snapshots/* (GetObject) in the build artifacts bucket."
  }
  assert {
    condition = toset(flatten([
      for policy in [aws_iam_role_policy.control_api.policy, aws_iam_role_policy.control_api_source_uploads.policy] : [for statement in jsondecode(policy).Statement : statement.Action]
      ])) == toset(["logs:GetLogEvents", "s3:PutObject", "s3:AbortMultipartUpload", "s3:GetObject"]) && alltrue([
      for policy in [aws_iam_role_policy.control_api.policy, aws_iam_role_policy.control_api_source_uploads.policy] : alltrue([for statement in jsondecode(policy).Statement : statement.Effect == "Allow"])
    ])
    error_message = "Control API must have no S3 delete/list, other S3, CodeBuild, ECR or Kubernetes permission beyond log read, uploads write and snapshots read."
  }
  assert {
    condition     = aws_iam_role.control_api.name != aws_iam_role.build_worker.name && aws_iam_role.control_api.name != aws_iam_role.deploy_worker.name
    error_message = "Control API must not share a role with the Build or Deploy Worker."
  }
}

run "onprem_ecr_pull_boundary" {
  command = apply
  assert {
    condition = aws_iam_role.onprem_ecr_pull.name == "${var.project}-${var.environment}-onprem-ecr-pull" && jsonencode(jsondecode(aws_iam_role.onprem_ecr_pull.assume_role_policy).Statement) == jsonencode([{
      Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Principal = { AWS = aws_iam_role.control_api.arn }
    }])
    error_message = "Only the Control API role may assume the on-prem ECR pull role; TagSession carries its transitive Pod Identity session tags."
  }
  assert {
    condition = aws_iam_role_policy.onprem_ecr_pull.role == aws_iam_role.onprem_ecr_pull.id && jsonencode(jsondecode(aws_iam_role_policy.onprem_ecr_pull.policy).Statement) == jsonencode([
      { Sid = "EcrAuthorizationToken", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Sid = "PullServiceImages", Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.project}/services/*" }
    ])
    error_message = "The on-prem ECR pull role may only get a token and pull user service images; no push, delete or platform repositories."
  }
  assert {
    condition = aws_iam_role_policy.control_api_onprem_ecr_pull.name == "assume-onprem-ecr-pull" && aws_iam_role_policy.control_api_onprem_ecr_pull.role == aws_iam_role.control_api.id && jsonencode(jsondecode(aws_iam_role_policy.control_api_onprem_ecr_pull.policy).Statement) == jsonencode([{
      Sid = "AssumeOnpremEcrPull", Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.onprem_ecr_pull.arn
    }]) && output.onprem_ecr_pull_role_arn == aws_iam_role.onprem_ecr_pull.arn
    error_message = "Control API may assume only the on-prem ECR pull role and gets no ECR permission itself."
  }
  assert {
    condition     = !contains([aws_iam_role.build_worker.name, aws_iam_role.deploy_worker.name, aws_iam_role.control_api.name], aws_iam_role.onprem_ecr_pull.name)
    error_message = "The on-prem ECR pull role is separate from every runtime role."
  }
}

run "build_worker_source_uploads_boundary" {
  command = apply
  assert {
    condition = jsonencode(one([for statement in jsondecode(aws_iam_role_policy.build_worker.policy).Statement : statement if statement.Sid == "ReadSourceUploads"])) == jsonencode({
      Sid = "ReadSourceUploads", Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.build_artifacts.arn}/uploads/*"
    })
    error_message = "Build Worker may only read uploads/* (s3:GetObject) for user-uploaded sources."
  }
  assert {
    condition = jsonencode(one([for statement in jsondecode(aws_iam_role_policy.build_worker.policy).Statement : statement if statement.Sid == "SourceSnapshots"])) == jsonencode({
      Sid = "SourceSnapshots", Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.build_artifacts.arn}/snapshots/*"
    })
    error_message = "Build Worker snapshot read/write on snapshots/* must stay unchanged."
  }
  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.build_worker.policy).Statement :
      !anytrue([for action in statement.Action : can(regex("^s3:(\\*|Put|Delete|Abort|List)", action))]) || statement.Resource == "${aws_s3_bucket.build_artifacts.arn}/snapshots/*"
    ])
    error_message = "Build Worker must never write, delete or list outside snapshots/*, in particular nothing under uploads/*."
  }
}
