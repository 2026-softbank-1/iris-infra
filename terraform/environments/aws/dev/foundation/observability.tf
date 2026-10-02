# 로그 저장(Loki): S3 버킷과 Loki Pod 역할. Pod Identity 연결은 management stack 에서 한다.
# 보관 기간(7일)은 Loki compactor 가 지운다. 버킷 lifecycle 로 지우면 index 와 어긋나므로 두지 않는다.

locals {
  loki_name = "${var.project}-${var.environment}-loki"
}

# 로그를 잃지 않도록 force_destroy 를 쓰지 않는다. 철거 시 직접 비운다.
resource "aws_s3_bucket" "loki" {
  bucket = "${local.loki_name}-${var.aws_account_id}-${var.aws_region}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "loki" {
  bucket = aws_s3_bucket.loki.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "loki" {
  bucket = aws_s3_bucket.loki.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "loki" {
  bucket = aws_s3_bucket.loki.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.loki.arn, "${aws_s3_bucket.loki.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.loki]
}

resource "aws_iam_role" "loki" {
  name        = local.loki_name
  description = "Loki (management EKS): store log chunks and index in S3"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
      Condition = { StringEquals = {
        "aws:RequestTag/eks-cluster-name"           = var.management_cluster_name
        "aws:RequestTag/kubernetes-namespace"       = "observability"
        "aws:RequestTag/kubernetes-service-account" = "loki"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "loki" {
  name = "loki-storage"
  role = aws_iam_role.loki.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "ListBucket", Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.loki.arn },
      {
        Sid      = "ReadWriteChunks"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.loki.arn}/*"
      },
    ]
  })
}
