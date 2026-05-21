variable "project_name" {
  description = "Tag + name prefix for all resources."
  type        = string
  default     = "nira-insig"
}

variable "aws_region" {
  description = "AWS region. Mumbai is the obvious choice for India."
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "EC2 instance size. t3.small is plenty for v0 (Postgres is on Neon)."
  type        = string
  default     = "t3.small"
}

variable "root_disk_gb" {
  description = "Root EBS volume size in GB. 20 leaves room for Docker images + uploads."
  type        = number
  default     = 20
}

variable "key_pair_name" {
  description = "Name of an EXISTING EC2 key pair in this region. Create it via the EC2 Console → Key Pairs before running terraform."
  type        = string
}

variable "ssh_allowed_cidr" {
  description = "CIDR allowed to SSH in. Use 'curl -s ifconfig.me/cidr' or 'YOUR.IP.HERE/32'. DO NOT use 0.0.0.0/0 in production."
  type        = string
}

variable "repo_url" {
  description = "Public HTTPS URL of the nira-insig git repo. Used by cloud-init to clone the code onto the EC2."
  type        = string
}

variable "repo_branch" {
  description = "Branch to deploy."
  type        = string
  default     = "main"
}
