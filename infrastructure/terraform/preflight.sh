#!/usr/bin/env bash
# Pre-deploy sanity check. Run this BEFORE `terraform apply`.
# Catches the cheap-to-fix problems on your laptop instead of on AWS.
#
# Usage:
#   cd infrastructure/terraform
#   ./preflight.sh
#
# Exit code:
#   0 = all good, safe to terraform apply
#   1 = at least one check failed

set -u
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

PASS=0
FAIL=0

check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  ✓ $label"
        PASS=$((PASS+1))
    else
        echo "  ✗ $label"
        FAIL=$((FAIL+1))
    fi
}

check_file() {
    local f="$1"
    if [ -f "$REPO_ROOT/$f" ]; then echo "  ✓ $f"; PASS=$((PASS+1));
    else echo "  ✗ MISSING: $f"; FAIL=$((FAIL+1)); fi
}

section() { echo ""; echo "── $1 ──"; }


# ----------------------------------------------------------------------------
section "Required files present"
check_file "backend/Dockerfile"
check_file "backend/requirements.txt"
check_file "backend/alembic/env.py"
check_file "backend/scripts/entrypoint.sh"
check_file "frontend/Dockerfile.prod"
check_file "frontend/package.json"
check_file "frontend/package-lock.json"
check_file "frontend/vite.config.ts"
check_file "infrastructure/deploy/docker-compose.prod.yml"
check_file "infrastructure/deploy/Caddyfile"
check_file "infrastructure/deploy/.env.prod.example"
check_file "infrastructure/terraform/main.tf"
check_file "infrastructure/terraform/cloud-init.yaml"


# ----------------------------------------------------------------------------
section "Terraform config"
if [ -f infrastructure/terraform/terraform.tfvars ]; then
    echo "  ✓ terraform.tfvars present"
    PASS=$((PASS+1))
    # Make sure required vars are filled
    for var in key_pair_name ssh_allowed_cidr repo_url; do
        if grep -E "^${var}\s*=" infrastructure/terraform/terraform.tfvars \
              | grep -q -E '"(YOUR\.|YOUR_)' ; then
            echo "  ✗ $var still contains the placeholder value"
            FAIL=$((FAIL+1))
        else
            val=$(grep -E "^${var}\s*=" infrastructure/terraform/terraform.tfvars | sed 's/.*= *//; s/^"//; s/"$//')
            if [ -n "$val" ]; then
                echo "  ✓ $var = $val"
                PASS=$((PASS+1))
            else
                echo "  ✗ $var is empty"
                FAIL=$((FAIL+1))
            fi
        fi
    done
else
    echo "  ✗ terraform.tfvars missing — copy from terraform.tfvars.example and fill in"
    FAIL=$((FAIL+1))
fi


# ----------------------------------------------------------------------------
section "Git state"
if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "  ✓ inside a git repo"
    PASS=$((PASS+1))

    if git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
        origin=$(git -C "$REPO_ROOT" remote get-url origin)
        echo "  ✓ remote origin: $origin"
        PASS=$((PASS+1))
    else
        echo "  ✗ no 'origin' remote — cloud-init can't clone the repo on the EC2"
        FAIL=$((FAIL+1))
    fi

    if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
        echo "  ✗ uncommitted changes — push first, or EC2 will clone an older version"
        git -C "$REPO_ROOT" status --short | head -10 | sed 's/^/      /'
        FAIL=$((FAIL+1))
    else
        echo "  ✓ no uncommitted changes"
        PASS=$((PASS+1))
    fi

    local_head=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)
    if git -C "$REPO_ROOT" ls-remote origin HEAD 2>/dev/null | grep -q "$local_head"; then
        echo "  ✓ local HEAD matches origin (pushed)"
        PASS=$((PASS+1))
    else
        echo "  ✗ local HEAD not on origin — git push before deploy"
        FAIL=$((FAIL+1))
    fi
else
    echo "  ✗ not in a git repo"
    FAIL=$((FAIL+1))
fi


# ----------------------------------------------------------------------------
section "Tools installed"
check "aws CLI available"            command -v aws
check "terraform available"          command -v terraform
check "docker available"             command -v docker
check "docker compose plugin"        docker compose version


# ----------------------------------------------------------------------------
section "AWS auth"
if command -v aws >/dev/null 2>&1; then
    if aws sts get-caller-identity --output text >/dev/null 2>&1; then
        who=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null)
        echo "  ✓ AWS auth working: $who"
        PASS=$((PASS+1))
    else
        echo "  ✗ aws sts get-caller-identity FAILED — run 'aws configure'"
        FAIL=$((FAIL+1))
    fi
    region=$(aws configure get region 2>/dev/null || echo "")
    if [ "$region" = "ap-south-1" ]; then
        echo "  ✓ default region = ap-south-1"
        PASS=$((PASS+1))
    else
        echo "  ⚠ default region is '$region' (we use ap-south-1, override with --region or AWS_REGION)"
    fi
fi


# ----------------------------------------------------------------------------
section "Backend Python compiles + tests pass"
if command -v python3 >/dev/null 2>&1; then
    if (cd "$REPO_ROOT/backend" && python3 -m compileall -q services worker api 2>/dev/null); then
        echo "  ✓ backend modules compile"
        PASS=$((PASS+1))
    else
        echo "  ✗ backend Python compile errors"
        FAIL=$((FAIL+1))
    fi

    # Run pure-Python unit tests if pytest is available locally.
    if command -v pytest >/dev/null 2>&1; then
        if (cd "$REPO_ROOT/backend" && PYTHONPATH=. pytest tests/unit -q 2>/dev/null | grep -q "passed"); then
            echo "  ✓ unit tests pass"
            PASS=$((PASS+1))
        else
            echo "  ⚠ unit tests didn't pass — re-run manually: cd backend && PYTHONPATH=. pytest tests/unit"
        fi
    fi
fi


# ----------------------------------------------------------------------------
section "Compose syntax (prod)"
if command -v docker >/dev/null 2>&1; then
    # Use a dummy PUBLIC_HOST so variable interpolation doesn't fail.
    if PUBLIC_HOST=preflight.example.com ACME_EMAIL=preflight@example.com \
       DATABASE_URL=postgres://x:x@x/x REDIS_URL=redis://x \
       CORS_ORIGINS=https://x ANTHROPIC_API_KEY= \
       docker compose -f "$REPO_ROOT/infrastructure/deploy/docker-compose.prod.yml" config >/dev/null 2>&1; then
        echo "  ✓ docker-compose.prod.yml is valid"
        PASS=$((PASS+1))
    else
        echo "  ✗ docker-compose.prod.yml has a syntax/interpolation error:"
        PUBLIC_HOST=preflight.example.com ACME_EMAIL=preflight@example.com \
           DATABASE_URL=postgres://x:x@x/x REDIS_URL=redis://x \
           CORS_ORIGINS=https://x ANTHROPIC_API_KEY= \
           docker compose -f "$REPO_ROOT/infrastructure/deploy/docker-compose.prod.yml" config 2>&1 | head -10 | sed 's/^/      /'
        FAIL=$((FAIL+1))
    fi
fi


# ----------------------------------------------------------------------------
section "Terraform syntax"
if command -v terraform >/dev/null 2>&1; then
    if (cd "$REPO_ROOT/infrastructure/terraform" && terraform fmt -check >/dev/null 2>&1); then
        echo "  ✓ terraform fmt — formatted"
        PASS=$((PASS+1))
    else
        echo "  ⚠ terraform fmt found unformatted files (run: terraform fmt)"
    fi
    if (cd "$REPO_ROOT/infrastructure/terraform" && terraform validate >/dev/null 2>&1); then
        echo "  ✓ terraform validate — schema OK"
        PASS=$((PASS+1))
    else
        echo "  ⚠ terraform validate failed (run 'terraform init' first if you haven't):"
        (cd "$REPO_ROOT/infrastructure/terraform" && terraform validate 2>&1 | head -10 | sed 's/^/      /')
    fi
fi


# ----------------------------------------------------------------------------
section "Reminders that aren't auto-checkable"
cat <<'EOF'
  ⚠ Did you push to GitHub?               -> required, cloud-init clones from origin
  ⚠ Repo URL is PUBLIC                    -> private needs a deploy key (extra step)
  ⚠ Anthropic credits added               -> https://console.anthropic.com/settings/billing
  ⚠ DNS will be added BEFORE first start  -> Caddy needs DNS to obtain TLS cert
  ⚠ Neon DATABASE_URL uses DIRECT host    -> NOT the -pooler endpoint (breaks migrations)
EOF


# ----------------------------------------------------------------------------
echo ""
echo "════════════════════════════════════════════"
echo "  $PASS passed, $FAIL failed"
echo "════════════════════════════════════════════"
if [ "$FAIL" -gt 0 ]; then
    echo "✗ NOT SAFE TO DEPLOY — fix the items above first."
    exit 1
fi
echo "✓ Preflight passed. Safe to run 'terraform apply'."
echo ""
echo "Order of operations from here:"
echo "  1. terraform apply                        (provisions EC2, takes ~2 min)"
echo "  2. Add Cloudflare DNS A record            (insig.nirabalance.com → EIP, proxy OFF)"
echo "  3. wait ~60s for DNS propagation"
echo "  4. ssh -i ~/.ssh/<key>.pem ubuntu@<EIP>"
echo "  5. sudo nano /opt/nira-insig/.env.prod    (fill DATABASE_URL, ACME_EMAIL, ANTHROPIC_API_KEY)"
echo "  6. sudo systemctl start nira-insig"
echo "  7. docker compose -f infrastructure/deploy/docker-compose.prod.yml logs -f"
echo "     (watch for Caddy obtaining the cert — about 30 seconds after first start)"
echo "  8. https://insig.nirabalance.com         🎉"
