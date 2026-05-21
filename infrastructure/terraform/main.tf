# Nira Insig — single-instance AWS deployment.
#
# Provisions exactly what's needed to run the docker-compose.prod.yml stack:
#   • One t3.small EC2 in the default VPC, ap-south-1 (Mumbai).
#   • An Elastic IP attached to it.
#   • A Security Group: 22 (SSH from your IP), 80, 443 (anywhere).
#   • cloud-init script that installs Docker, clones the repo, drops a
#     systemd unit, and sets up the host filesystem.
#
# Out of scope (intentional — we use external services):
#   • Postgres → Neon (managed)
#   • DNS      → Cloudflare
#   • TLS      → Caddy + Let's Encrypt (runs inside the stack)
#   • Object storage → not needed yet; uploads live on EBS.
#
# Cost (24/7):
#   t3.small + 20 GB EBS + EIP + ~10 GB egress ≈ ₹1,500–1,800 / month.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# Look up the default VPC and pick any default subnet in the region.
# ---------------------------------------------------------------------------

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Latest Ubuntu 24.04 LTS AMI for the region (Canonical's official account).
data "aws_ami" "ubuntu_2404" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# ---------------------------------------------------------------------------
# Security Group
# ---------------------------------------------------------------------------

resource "aws_security_group" "app" {
  name        = "${var.project_name}-sg"
  description = "Nira Insig - allow SSH from your IP and HTTP/HTTPS from anywhere"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_allowed_cidr]
  }

  ingress {
    description = "HTTP (ACME challenge + redirect to https)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
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
    Project = var.project_name
  }
}

# ---------------------------------------------------------------------------
# cloud-init — runs on the EC2's first boot.
# ---------------------------------------------------------------------------

locals {
  cloud_init = templatefile("${path.module}/cloud-init.yaml", {
    repo_url    = var.repo_url
    repo_branch = var.repo_branch
  })
}

# ---------------------------------------------------------------------------
# EC2 instance
# ---------------------------------------------------------------------------

resource "aws_instance" "app" {
  ami                         = data.aws_ami.ubuntu_2404.id
  instance_type               = var.instance_type
  subnet_id                   = tolist(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids      = [aws_security_group.app.id]
  key_name                    = var.key_pair_name
  associate_public_ip_address = true
  user_data                   = local.cloud_init
  user_data_replace_on_change = false # don't rebuild on cloud-init edits

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_disk_gb
    delete_on_termination = true
    encrypted             = true
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  tags = {
    Name    = var.project_name
    Project = var.project_name
  }
}

# ---------------------------------------------------------------------------
# Elastic IP — stable address survives stop/start.
# ---------------------------------------------------------------------------

resource "aws_eip" "app" {
  domain = "vpc"

  tags = {
    Name    = "${var.project_name}-eip"
    Project = var.project_name
  }
}

resource "aws_eip_association" "app" {
  instance_id   = aws_instance.app.id
  allocation_id = aws_eip.app.id
}
