###############################################################
# main.tf
# AI News NER Pipeline — Minimal Architecture
# Resources: S3 bucket + EC2 t2.micro + IAM role
###############################################################

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # CICD LEARNING: Remote state stored in S3 so GitHub Actions
  # can read/write state without local files.
  # Create this bucket MANUALLY once before running terraform init.
  backend "s3" {
    bucket = "ai-news-mlops-tfstate"   # change to your bucket name
    key    = "state/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

###############################################################
# 1. S3 BUCKET  — single bucket, all data lives here
###############################################################

resource "aws_s3_bucket" "main" {
  bucket = var.s3_bucket_name

  tags = {
    Project     = "ai-news-ner"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# Block all public access — private data only
resource "aws_s3_bucket_public_access_block" "main" {
  bucket = aws_s3_bucket.main.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning — every object keeps history
# Lets you roll back model artifacts, raw data, or configs
resource "aws_s3_bucket_versioning" "main" {
  bucket = aws_s3_bucket.main.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Lifecycle rule: auto-delete raw articles after 30 days to stay free
resource "aws_s3_bucket_lifecycle_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    id     = "expire-raw-articles"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "expire-label-queue"
    status = "Enabled"

    filter {
      prefix = "label-queue/"
    }

    expiration {
      days = 60
    }
  }
}

###############################################################
# S3 FOLDER STRUCTURE (created via empty placeholder objects)
#
# ai-news-mlops-bucket/
#   raw/          ← raw article JSON from news APIs
#   processed/    ← cleaned articles ready for NER
#   entities/     ← NER output JSON per batch (week/date)
#   graphs/       ← knowledge graph PNG + JSON exports
#   drift/        ← confidence score logs per batch
#   label-queue/  ← low-confidence spans awaiting annotation
#   labeled/      ← annotated spans (CoNLL format)
#   models/       ← fine-tuned model artifacts
#   eval/         ← F1 evaluation results per model version
###############################################################

locals {
  s3_folders = [
    "raw/.keep",
    "processed/.keep",
    "entities/.keep",
    "graphs/.keep",
    "drift/.keep",
    "label-queue/.keep",
    "labeled/.keep",
    "models/.keep",
    "eval/.keep",
  ]
}

resource "aws_s3_object" "folders" {
  for_each = toset(local.s3_folders)
  bucket   = aws_s3_bucket.main.id
  key      = each.value
  content  = ""
}

###############################################################
# 2. IAM ROLE  — EC2 instance can read/write S3 bucket only
###############################################################

resource "aws_iam_role" "ec2_role" {
  name = "ai-news-ner-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Project   = "ai-news-ner"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_policy" "s3_policy" {
  name        = "ai-news-ner-s3-policy"
  description = "Allow EC2 to read/write the NER pipeline S3 bucket"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetObjectVersion",
        ]
        Resource = [
          aws_s3_bucket.main.arn,
          "${aws_s3_bucket.main.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_s3" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = aws_iam_policy.s3_policy.arn
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ai-news-ner-ec2-profile"
  role = aws_iam_role.ec2_role.name
}

###############################################################
# 3. SECURITY GROUP  — SSH from anywhere, outbound unrestricted
# In production: restrict SSH to your IP only
###############################################################

resource "aws_security_group" "ec2_sg" {
  name        = "ai-news-ner-sg"
  description = "Security group for NER pipeline EC2 instance"

  ingress {
    description = "SSH access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]   # TODO: restrict to your IP in production
  }

  ingress {
    description = "Streamlit dashboard"
    from_port   = 8501
    to_port     = 8501
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project   = "ai-news-ner"
    ManagedBy = "terraform"
  }
}

###############################################################
# 4. EC2 INSTANCE  — t2.micro (free tier)
# user_data bootstraps Python, installs deps, clones repo
###############################################################

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical (Ubuntu)

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "ner_pipeline" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = "t2.micro"
  key_name               = var.key_pair_name
  iam_instance_profile   = aws_iam_instance_profile.ec2_profile.name
  vpc_security_group_ids = [aws_security_group.ec2_sg.id]

  # Bootstrap script — runs once on first launch
  user_data = <<-EOF
    #!/bin/bash
    set -e

    # System packages
    apt-get update -y
    apt-get install -y python3-pip python3-venv git

    # Create app directory
    mkdir -p /opt/ai-news-ner
    cd /opt/ai-news-ner

    # Python virtual environment
    python3 -m venv venv
    source venv/bin/activate

    # Install pipeline dependencies
    pip install --upgrade pip
    pip install \
      boto3 \
      feedparser \
      transformers \
      torch --index-url https://download.pytorch.org/whl/cpu \
      spacy \
      networkx \
      matplotlib \
      streamlit \
      evidently \
      mlflow \
      requests

    # Download spaCy model
    python -m spacy download en_core_web_sm

    # Set environment variable for S3 bucket
    echo "export S3_BUCKET=${var.s3_bucket_name}" >> /etc/environment
    echo "export AWS_DEFAULT_REGION=${var.aws_region}" >> /etc/environment

    # Set up cron job for hourly ingestion
    echo "0 * * * * ubuntu /opt/ai-news-ner/venv/bin/python /opt/ai-news-ner/pipeline/ingest.py >> /var/log/ner-ingest.log 2>&1" | crontab -

    echo "Bootstrap complete" >> /var/log/user-data.log
  EOF

  tags = {
    Name        = "ai-news-ner-pipeline"
    Project     = "ai-news-ner"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
