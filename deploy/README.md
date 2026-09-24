# Deploying CodeSage

CodeSage ships as a Docker stack (app + Postgres/pgvector + Caddy reverse proxy),
so the same artifact runs on a DigitalOcean droplet, AWS ECS, or any Docker host.
This directory automates the **DigitalOcean droplet** path.

## TL;DR (DigitalOcean)

```bash
# 1. One-time tooling
brew install doctl                 # or see DO docs for your platform
doctl auth init                    # paste your DigitalOcean API token
ssh-keygen -t ed25519              # if you don't already have a key

# 2. Configure
cp .env.example .env               # add ANTHROPIC_API_KEY, CODESAGE_API_KEYS, a strong POSTGRES_PASSWORD

# 3. Provision + deploy (creates the droplet, installs Docker, ships the stack)
./deploy/deploy.sh
```

When it finishes you'll get a public IP:

```
http://<ip>/         root
http://<ip>/docs     interactive API docs
http://<ip>/healthz  health check
```

Re-deploy after changes:

```bash
./deploy/deploy.sh --redeploy <ip>
```

## What `deploy.sh` does

1. Registers your SSH public key with DigitalOcean (if not already present).
2. Creates an Ubuntu 24.04 droplet, passing [`cloud-init.yaml`](cloud-init.yaml)
   as user-data — which installs Docker Engine + Compose, creates a `deploy`
   user, and locks the firewall down to SSH/HTTP/HTTPS.
3. Ships a clean snapshot of the repo (via `git archive`) plus your `.env`.
4. Runs `docker compose -f deploy/docker-compose.prod.yml up -d --build` on the
   droplet.

Tunables via environment variables: `DROPLET_NAME`, `REGION`, `SIZE`, `IMAGE`,
`SSH_KEY_NAME`, `SSH_KEY_PATH`. Defaults: `codesage` / `nyc1` / `s-2vcpu-4gb` /
`ubuntu-24-04-x64`.

## HTTPS with a custom domain

1. Point an A record at the droplet IP.
2. Set `CODESAGE_DOMAIN=codesage.example.com` in `.env`.
3. Re-deploy. Caddy obtains and renews a Let's Encrypt certificate automatically.

Without `CODESAGE_DOMAIN`, Caddy serves plain HTTP on port 80 (fine for an
IP-only demo droplet).

## The production stack

[`docker-compose.prod.yml`](docker-compose.prod.yml):

| Service | Notes |
| --- | --- |
| `db` | `pgvector/pgvector:pg16`, not published to the host, persistent volume |
| `api` | the CodeSage image, only reachable via the proxy |
| `caddy` | reverse proxy on :80/:443, automatic HTTPS when a domain is set |

## AWS note (matches the résumé framing)

The image is cloud-agnostic. To run the same container on AWS:

- **ECS/Fargate**: push the image to ECR, run the `api` task behind an ALB, use
  RDS Postgres with the `vector` extension, and inject config via task env /
  Secrets Manager.
- **Lambda**: wrap `app.main:app` with an ASGI adapter (e.g. Mangum) for the
  REST surface; keep long-running reviews on ECS.

Nothing in the application code is DigitalOcean-specific — only this directory is.

## Teardown

```bash
doctl compute droplet delete codesage
```
