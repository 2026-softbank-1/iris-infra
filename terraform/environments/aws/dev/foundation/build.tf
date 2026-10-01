# 사용자 서비스 이미지 빌드: Build Worker → S3 소스 스냅샷 → CodeBuild → ECR(iris/services/*).
# ECR 저장소는 서비스마다 Build Worker 가 만든다. 여기서는 접두어 단위 권한만 건다.

locals {
  build_name              = "${var.project}-${var.environment}-build"
  service_repository_arns = "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.project}/services/*"
}

resource "aws_s3_bucket" "build_artifacts" {
  bucket        = "${local.build_name}-artifacts-${var.aws_account_id}-${var.aws_region}"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "build_artifacts" {
  bucket = aws_s3_bucket.build_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "build_artifacts" {
  bucket = aws_s3_bucket.build_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 스냅샷은 빌드 시작 직후에만 쓴다. 하루 뒤 지운다.
resource "aws_s3_bucket_lifecycle_configuration" "build_artifacts" {
  bucket = aws_s3_bucket.build_artifacts.id

  rule {
    id     = "expire-build-inputs"
    status = "Enabled"
    filter {}

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "build_artifacts" {
  bucket = aws_s3_bucket.build_artifacts.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.build_artifacts.arn, "${aws_s3_bucket.build_artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.build_artifacts]
}

resource "aws_cloudwatch_log_group" "build" {
  name              = "/aws/codebuild/${local.build_name}"
  retention_in_days = 30
}

# CodeBuild 는 소스를 presigned URL 로 받으므로 S3 권한이 없다. DB·GitHub·GitOps 권한도 주지 않는다.
resource "aws_iam_role" "codebuild" {
  name        = "${local.build_name}-codebuild"
  description = "CodeBuild service role: push user service images to ECR"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "codebuild.amazonaws.com" }
      Condition = { StringEquals = { "aws:SourceAccount" = var.aws_account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "codebuild" {
  name = "build-and-push"
  role = aws_iam_role.codebuild.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteBuildLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.build.arn}:*"
      },
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        # 레지스트리 캐시(:cache)를 읽고 이미지를 push 한다.
        # 신뢰할 수 없는 빌드가 다른 서비스 저장소에도 push 할 수 있다. 서비스별 격리는 후속 과제다.
        Sid    = "PushServiceImages"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
        ]
        Resource = local.service_repository_arns
      },
    ]
  })
}

resource "aws_codebuild_project" "build" {
  name                   = local.build_name
  description            = "Build user service images (Dockerfile or Railpack) and push to ECR"
  service_role           = aws_iam_role.codebuild.arn
  build_timeout          = 15
  queued_timeout         = 30
  concurrent_build_limit = 10

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/buildspec.yml")
  }

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type         = "LINUX_CONTAINER"
    compute_type = "BUILD_GENERAL1_MEDIUM"
    # TODO: Railpack CLI 를 넣은 커스텀 이미지(iris/build-images/railpack)로 바꾼다. 지금은 Dockerfile 빌드만 된다.
    image                       = "aws/codebuild/amazonlinux-x86_64-standard:5.0"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = true
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.build.name
    }
  }
}

# Build Worker(관리 EKS Pod)용. Pod Identity 연결은 management stack 에서 한다.
resource "aws_iam_role" "build_worker" {
  name        = "${local.build_name}-worker"
  description = "Iris Build Worker: start CodeBuild, upload source snapshots, read ECR digests"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "build_worker" {
  name = "build-orchestration"
  role = aws_iam_role.build_worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunBuilds"
        Effect   = "Allow"
        Action   = ["codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:StopBuild"]
        Resource = aws_codebuild_project.build.arn
      },
      {
        Sid      = "SourceSnapshots"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.build_artifacts.arn}/snapshots/*"
      },
      {
        # ECR push 권한은 없다. push 는 CodeBuild 역할만 한다.
        Sid    = "ManageServiceRepositories"
        Effect = "Allow"
        Action = [
          "ecr:CreateRepository",
          "ecr:DescribeRepositories",
          "ecr:PutLifecyclePolicy",
          "ecr:DescribeImages",
        ]
        Resource = local.service_repository_arns
      },
    ]
  })
}
