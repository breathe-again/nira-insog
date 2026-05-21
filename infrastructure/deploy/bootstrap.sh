#!/usr/bin/env bash
# Bootstrap script — run this ONCE on a fresh Ubuntu 24.04 EC2.
# Idempotent: safe to re-run.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/YOUR_USER/nira-insig.git}"  # OVERRIDE THIS
INSTALL_DIR="/opt/nira-insig"
UPLOADS_DIR="/var/nira/uploads"

echo "==> Updating apt + installing essentials"
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg lsb-release ufw fail2ban

echo "==> Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER"
fi

echo "==> Creating uploads dir on host"
sudo mkdir -p "$UPLOADS_DIR"
sudo chown -R "$USER":"$USER" "$UPLOADS_DIR"

echo "==> Cloning repo to $INSTALL_DIR"
if [ ! -d "$INSTALL_DIR/.git" ]; then
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown "$USER":"$USER" "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    git -C "$INSTALL_DIR" pull --rebase
fi

echo "==> Firewall — allow 22, 80, 443 only"
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo "==> Setting up systemd unit so the stack survives reboots"
sudo tee /etc/systemd/system/nira-insig.service > /dev/null <<EOF
[Unit]
Description=Nira Insig (docker compose prod stack)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env.prod
ExecStart=/usr/bin/docker compose -f infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod up -d --build
ExecStop=/usr/bin/docker compose -f infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod down

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nira-insig.service

echo ""
echo "==> Bootstrap complete."
echo ""
echo "Next steps:"
echo "  1. Copy infrastructure/deploy/.env.prod.example to $INSTALL_DIR/.env.prod"
echo "  2. Fill in DATABASE_URL (Neon pooled), PUBLIC_HOST, ACME_EMAIL."
echo "  3. Point insig.nirabalance.com -> this server's public IP in Cloudflare."
echo "  4. Start the stack:   sudo systemctl start nira-insig"
echo "  5. Watch logs:        cd $INSTALL_DIR && docker compose -f infrastructure/deploy/docker-compose.prod.yml logs -f"
echo ""
echo "NOTE: log out + back in once so your shell picks up docker group membership."
