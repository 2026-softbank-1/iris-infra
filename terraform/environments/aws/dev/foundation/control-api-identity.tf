# Control API shows build logs on the deployment details screen (iris-was ADR 0021) and stores the
# source archives that `likelion up` uploads (iris-was ADR 0023).
# It reads CloudWatch log events, writes S3 objects under `uploads/` and reads those under `snapshots/`.
# No CodeBuild, ECR or Kubernetes access, no log writes and no S3 delete or list.
# The Build Worker role has its own statements (log read, `uploads/` read) and is never shared.
resource "aws_iam_role" "control_api" {
  name        = "${var.project}-${var.environment}-control-api"
  description = "Iris Control API: read CodeBuild logs for the deployment details screen"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
        "aws:RequestTag/kubernetes-namespace"       = "iris-platform"
        "aws:RequestTag/kubernetes-service-account" = "iris-platform-api"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "control_api" {
  name = "read-build-logs"
  role = aws_iam_role.control_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      # The group ARN with `:*` covers its log streams. Reads only.
      Sid      = "ReadBuildLogs"
      Effect   = "Allow"
      Action   = ["logs:GetLogEvents"]
      Resource = "${aws_cloudwatch_log_group.build.arn}:*"
    }]
  })
}

# A separate policy keeps `read-build-logs` untouched. The bucket lifecycle expires objects after a day,
# so the API never needs s3:DeleteObject or s3:ListBucket.
resource "aws_iam_role_policy" "control_api_source_uploads" {
  name = "source-uploads"
  role = aws_iam_role.control_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteSourceUploads"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "${aws_s3_bucket.build_artifacts.arn}/uploads/*"
      },
      {
        # AI diagnosis hands the source snapshot to the agent as a presigned URL (iris-was ADR 0020).
        Sid      = "ReadSnapshotsForDiagnosis"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.build_artifacts.arn}/snapshots/*"
      },
    ]
  })
}

output "control_api_role_arn" {
  description = "Control API management Pod Identity role (iris-platform/iris-platform-api)"
  value       = aws_iam_role.control_api.arn
}
