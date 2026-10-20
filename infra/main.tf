terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  default = "us-east-1"
}

variable "api_url" {
  type = string
}

variable "api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "total_requests" {
  default = 10000000
}

variable "step" {
  default = 100
}

variable "qps_per_worker" {
  default = 0.95
}

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name for results"
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023*-arm64"]
  }
}

resource "aws_s3_bucket" "results" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket                  = aws_s3_bucket.results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "archive_file" "code" {
  type        = "zip"
  output_path = "${path.module}/code.zip"
  source_dir  = "${path.module}/.."
  excludes    = ["infra", ".git", "tests", ".pytest_cache", "__pycache__"]
}

resource "aws_s3_object" "code" {
  bucket = aws_s3_bucket.results.id
  key    = "code/code.zip"
  source = data.archive_file.code.output_path
  etag   = data.archive_file.code.output_md5
}

resource "aws_ssm_parameter" "api_key" {
  name  = "/throttled-api-harvester/api_key"
  type  = "SecureString"
  value = var.api_key == "" ? "none" : var.api_key
}

resource "aws_iam_role" "worker" {
  name = "throttled-api-harvester-worker"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "worker" {
  role = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.results.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = aws_ssm_parameter.api_key.arn
      }
    ]
  })
}

resource "aws_iam_instance_profile" "worker" {
  name = "throttled-api-harvester-worker"
  role = aws_iam_role.worker.name
}

# No inbound rules: workers only make outbound calls (SSM Session Manager can be added for access).
resource "aws_security_group" "worker" {
  name        = "throttled-api-harvester-worker"
  description = "Outbound only"
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "worker" {
  count                  = 2
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t4g.nano"
  iam_instance_profile   = aws_iam_instance_profile.worker.name
  vpc_security_group_ids = [aws_security_group.worker.id]

  user_data_replace_on_change = true
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    dnf install -y python3-pip unzip
    mkdir -p /opt/getter && cd /opt/getter
    aws s3 cp s3://${aws_s3_bucket.results.id}/${aws_s3_object.code.key} code.zip
    unzip -o code.zip
    pip3 install .
    cat > /etc/getter.env <<EOF
    API_URL=${var.api_url}
    BUCKET=${aws_s3_bucket.results.id}
    TOTAL_REQUESTS=${var.total_requests}
    STEP=${var.step}
    QPS=${var.qps_per_worker}
    WORKER_INDEX=${count.index}
    WORKER_COUNT=2
    AWS_DEFAULT_REGION=${var.region}
    EOF
    cat > /etc/systemd/system/getter.service <<'EOF'
    [Unit]
    Description=throttled-api-harvester worker
    After=network-online.target
    [Service]
    EnvironmentFile=/etc/getter.env
    ExecStartPre=/bin/bash -c 'echo "API_KEY=$(aws ssm get-parameter --name /throttled-api-harvester/api_key --with-decryption --query Parameter.Value --output text --region ${var.region})" > /run/getter-key.env'
    EnvironmentFile=/run/getter-key.env
    ExecStart=/usr/local/bin/getter
    Restart=on-failure
    RestartSec=30
    [Install]
    WantedBy=multi-user.target
    EOF
    systemctl daemon-reload
    systemctl enable --now getter
  EOT
}

output "bucket" {
  value = aws_s3_bucket.results.id
}
