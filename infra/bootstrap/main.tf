# Bootstrap: crea solo il bucket dello state Terraform. State locale (gitignorato).
# Uso: terraform -chdir=infra/bootstrap init && terraform -chdir=infra/bootstrap apply

terraform {
  required_version = ">= 1.10"

  # State nello stesso bucket che crea (migrato dallo state locale il 2026-11-19).
  # Per ricrearlo da zero: commentare il backend, apply con state locale, poi init -migrate-state.
  backend "s3" {
    bucket       = "gymbooking-tfstate-163876584948"
    key          = "gymbooking/bootstrap.tfstate"
    region       = "eu-north-1"
    profile      = "aws-personale"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "region" {
  type    = string
  default = "eu-north-1"
}

variable "profile" {
  type    = string
  default = "aws-personale"
}

provider "aws" {
  region  = var.region
  profile = var.profile
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "state" {
  bucket = "gymbooking-tfstate-${data.aws_caller_identity.current.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

output "state_bucket" {
  value = aws_s3_bucket.state.id
}
