# Terraform — Nira Insig AWS deployment

One-command provisioning for the production EC2 host.

## What this creates

| Resource | Purpose |
|---|---|
| `aws_instance.app`        | t3.small EC2 in ap-south-1, Ubuntu 24.04 |
| `aws_eip.app`             | Elastic IP (stable address) |
| `aws_eip_association.app` | Attaches the EIP to the instance |
| `aws_security_group.app`  | SSH from your IP only, 80/443 from anywhere |

cloud-init (see `cloud-init.yaml`) runs on first boot and installs Docker,
clones the repo, sets up the firewall, and stages a systemd unit. The
**stack is NOT started** until you SSH in and fill `/opt/nira-insig/.env.prod`.

## What this deliberately does NOT create

- **No Postgres** — we use Neon (managed).
- **No DNS** — you manage `nirabalance.com` in Cloudflare.
- **No Route 53** — same reason.
- **No ALB/CloudFront/S3** — single-instance v0; add later when you have load.

## Prerequisites

1. **AWS CLI installed and configured** for an IAM user with `AdministratorAccess`:
   ```bash
   brew install awscli terraform
   aws configure
   # Enter your access key + secret. Region: ap-south-1.
   ```
2. **An EC2 key pair already created** in the ap-south-1 region. EC2 Console →
   Key Pairs → Create → save the `.pem` to `~/.ssh/`, `chmod 400` it.
3. **Public git repo** for nira-insig. (If yours is private, set up a deploy
   key on the EC2 after `terraform apply` and re-run `git clone` manually.)

## Usage

```bash
cd infrastructure/terraform

# One-time: copy + fill in the example tfvars.
cp terraform.tfvars.example terraform.tfvars
nano terraform.tfvars     # set key_pair_name, ssh_allowed_cidr, repo_url

# Initialize providers (downloads ~50 MB).
terraform init

# Preview what will happen.
terraform plan

# Apply — takes ~2 minutes.
terraform apply
# (Type "yes" when prompted.)
```

Terraform prints the public IP and an SSH command when it's done. Add the
Cloudflare DNS record, then SSH in and finish `.env.prod`.

## Updating the stack later

cloud-init runs once at boot. To deploy code changes after the initial provision:

```bash
ssh ubuntu@<EIP>
cd /opt/nira-insig
git pull
sudo systemctl restart nira-insig
```

For environment changes:

```bash
ssh ubuntu@<EIP>
sudo nano /opt/nira-insig/.env.prod
sudo systemctl restart nira-insig
```

## Tear-down

```bash
terraform destroy
```

Destroys everything Terraform created. **You'll lose the EBS volume** with
the uploaded files, so back them up first (`s3 sync` or `scp -r`). Your
Neon database is separate and unaffected.

## Cost summary (steady-state, 24/7)

| Resource | ₹/month | $/month |
|---|---|---|
| t3.small (always on) | ~1,200 | ~14 |
| 20 GB gp3 EBS        | ~160   | ~2  |
| Elastic IP (attached) | 0     | 0   |
| 10 GB egress         | ~80    | ~1  |
| **AWS total**        | **~1,440** | **~17** |
| Neon (free tier)     | 0      | 0   |
| Cloudflare           | 0      | 0   |
| **Grand total**      | **~1,440** | **~17** |

Stop the instance when not in use (`aws ec2 stop-instances --instance-ids ...`)
to drop the bill to ~₹160/month (just EBS storage).
