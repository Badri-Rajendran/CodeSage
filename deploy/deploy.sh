#!/usr/bin/env bash
#
# Provision a fresh DigitalOcean droplet and deploy CodeSage to it.
#
# Prerequisites (on your machine):
#   - doctl installed and authenticated:  doctl auth init   (uses your DO API token)
#   - an SSH key registered with DigitalOcean (or this script imports one)
#   - a populated .env in the project root (cp .env.example .env; add ANTHROPIC_API_KEY)
#
# Usage:
#   ./deploy/deploy.sh                 # create droplet + deploy
#   ./deploy/deploy.sh --redeploy IP   # redeploy to an existing droplet at IP
#
# Tunables (env vars):
#   DROPLET_NAME (codesage)  REGION (nyc1)  SIZE (s-2vcpu-4gb)
#   IMAGE (ubuntu-24-04-x64)  SSH_KEY_NAME (codesage-deploy)  SSH_KEY_PATH (~/.ssh/id_ed25519)
set -euo pipefail

DROPLET_NAME="${DROPLET_NAME:-codesage}"
REGION="${REGION:-nyc1}"
SIZE="${SIZE:-s-2vcpu-4gb}"
IMAGE="${IMAGE:-ubuntu-24-04-x64}"
SSH_KEY_NAME="${SSH_KEY_NAME:-codesage-deploy}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="/opt/codesage"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log()  { printf "\033[36m▸ %s\033[0m\n" "$*"; }
die()  { printf "\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
command -v doctl >/dev/null 2>&1 || die "doctl not found. Install: https://docs.digitalocean.com/reference/doctl/how-to/install/"
command -v ssh   >/dev/null 2>&1 || die "ssh not found."
[[ -f "$PROJECT_ROOT/.env" ]] || die "Missing $PROJECT_ROOT/.env (cp .env.example .env and fill it in)."
grep -q "ANTHROPIC_API_KEY=sk-" "$PROJECT_ROOT/.env" || log "Warning: ANTHROPIC_API_KEY may be unset in .env (CodeSage will run in stub mode)."
grep -Eq '^CODESAGE_API_KEYS=.{16,}' "$PROJECT_ROOT/.env" \
  || die "Set CODESAGE_API_KEYS in .env (16+ chars) before deploying; the API is public. Generate: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
grep -Eqi '^CODESAGE_AUTH_DISABLED=(true|1|yes)' "$PROJECT_ROOT/.env" \
  && die "CODESAGE_AUTH_DISABLED is set in .env; refusing to deploy an unauthenticated API."

deploy_to() {
  local ip="$1"
  log "Waiting for SSH on $ip ..."
  for _ in $(seq 1 30); do
    if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -i "$SSH_KEY_PATH" "deploy@$ip" true 2>/dev/null; then
      break
    fi
    sleep 10
  done

  log "Waiting for Docker to be ready on the droplet ..."
  ssh -i "$SSH_KEY_PATH" "deploy@$ip" 'for i in $(seq 1 30); do command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && exit 0; sleep 10; done; exit 1' \
    || die "Docker did not become ready (cloud-init may still be running; retry with --redeploy $ip)."

  log "Shipping source to $ip:$REMOTE_DIR ..."
  ssh -i "$SSH_KEY_PATH" "deploy@$ip" "mkdir -p $REMOTE_DIR"
  # Ship a clean snapshot (git archive if available, else rsync without junk).
  if git -C "$PROJECT_ROOT" rev-parse >/dev/null 2>&1; then
    git -C "$PROJECT_ROOT" archive --format=tar HEAD | ssh -i "$SSH_KEY_PATH" "deploy@$ip" "tar -x -C $REMOTE_DIR"
  else
    rsync -az --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
      -e "ssh -i $SSH_KEY_PATH" "$PROJECT_ROOT/" "deploy@$ip:$REMOTE_DIR/"
  fi
  # .env is gitignored — copy it explicitly.
  scp -i "$SSH_KEY_PATH" "$PROJECT_ROOT/.env" "deploy@$ip:$REMOTE_DIR/.env"

  log "Building and starting the stack on the droplet ..."
  ssh -i "$SSH_KEY_PATH" "deploy@$ip" \
    "cd $REMOTE_DIR && docker compose -f deploy/docker-compose.prod.yml up -d --build"

  log "Deployed. CodeSage is live:"
  echo "    http://$ip/         (root)"
  echo "    http://$ip/docs     (interactive API docs)"
  echo "    http://$ip/healthz  (health check)"
}

# ── Redeploy path ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--redeploy" ]]; then
  [[ -n "${2:-}" ]] || die "Usage: $0 --redeploy <droplet-ip>"
  deploy_to "$2"
  exit 0
fi

# ── Ensure SSH key is registered with DigitalOcean ───────────────────────────
[[ -f "$SSH_KEY_PATH.pub" ]] || die "SSH public key not found at $SSH_KEY_PATH.pub (generate one: ssh-keygen -t ed25519)."
if ! doctl compute ssh-key list --format Name --no-header | grep -qx "$SSH_KEY_NAME"; then
  log "Importing SSH key '$SSH_KEY_NAME' into DigitalOcean ..."
  doctl compute ssh-key import "$SSH_KEY_NAME" --public-key-file "$SSH_KEY_PATH.pub"
fi
SSH_KEY_ID="$(doctl compute ssh-key list --format ID,Name --no-header | awk -v n="$SSH_KEY_NAME" '$2==n {print $1}')"
[[ -n "$SSH_KEY_ID" ]] || die "Could not resolve SSH key id for '$SSH_KEY_NAME'."

# ── Create the droplet ───────────────────────────────────────────────────────
if doctl compute droplet list --format Name --no-header | grep -qx "$DROPLET_NAME"; then
  log "Droplet '$DROPLET_NAME' already exists; reusing it."
else
  log "Creating droplet '$DROPLET_NAME' ($SIZE, $REGION, $IMAGE) ..."
  doctl compute droplet create "$DROPLET_NAME" \
    --region "$REGION" --size "$SIZE" --image "$IMAGE" \
    --ssh-keys "$SSH_KEY_ID" \
    --user-data-file "$SCRIPT_DIR/cloud-init.yaml" \
    --wait
fi

IP="$(doctl compute droplet get "$DROPLET_NAME" --format PublicIPv4 --no-header)"
[[ -n "$IP" ]] || die "Could not determine droplet IP."
log "Droplet IP: $IP"

deploy_to "$IP"
