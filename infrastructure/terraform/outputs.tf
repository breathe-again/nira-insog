output "public_ip" {
  description = "Elastic IP of the EC2. Point your DNS A-record here."
  value       = aws_eip.app.public_ip
}

output "ssh_command" {
  description = "Ready-to-paste SSH command (assumes ~/.ssh/<key>.pem)."
  value       = "ssh -i ~/.ssh/${var.key_pair_name}.pem ubuntu@${aws_eip.app.public_ip}"
}

output "dns_record" {
  description = "What to add in Cloudflare."
  value       = "A    insig    ${aws_eip.app.public_ip}    (Proxy status: DNS only — required for Let's Encrypt)"
}

output "next_steps" {
  description = "Once `terraform apply` succeeds."
  value       = <<-EOT

    EC2 is up. Next:

    1. Add a Cloudflare DNS record:
         A   insig   ${aws_eip.app.public_ip}   (Proxy status: DNS only)

    2. Wait ~60s for DNS to propagate, then SSH in:
         ssh -i ~/.ssh/${var.key_pair_name}.pem ubuntu@${aws_eip.app.public_ip}

    3. Inside the server:
         sudo nano /opt/nira-insig/.env.prod
         # Fill in DATABASE_URL (Neon), PUBLIC_HOST, ACME_EMAIL.

    4. Start the stack:
         sudo systemctl start nira-insig

    5. Watch logs:
         cd /opt/nira-insig
         docker compose -f infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod logs -f

    6. Open https://insig.nirabalance.com once Caddy obtains the cert (~30s).
  EOT
}
