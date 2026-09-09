#!/usr/bin/env bash
# Boathouse host install. Idempotent. Run as root on the box after the repo is at /srv/boathouse.
set -euo pipefail
cd /srv/boathouse

# Many small networks (one per tool) instead of Docker's default fifteen /16s.
if [ ! -f /etc/docker/daemon.json ] || ! grep -q default-address-pools /etc/docker/daemon.json; then
  cat > /etc/docker/daemon.json <<'JSON'
{ "default-address-pools": [ { "base": "10.200.0.0/16", "size": 24 } ],
  "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
  systemctl restart docker
fi

mkdir -p /var/lib/boathouse/state /var/lib/boathouse/backups /etc/boathouse && chmod 700 /var/lib/boathouse/state /etc/boathouse
if [ ! -f .env ]; then
  cat > .env <<ENV
BH_DOMAIN=${BH_DOMAIN:?set BH_DOMAIN}
BH_WORKSPACE=${BH_WORKSPACE:-starter}
BH_WORKSPACE_NAME=${BH_WORKSPACE_NAME:-Starter}
BH_OWNERS=${BH_OWNERS:?set BH_OWNERS}
BH_PUBLIC_IP=${BH_PUBLIC_IP:-$(curl -fsS https://api.porkbun.com/api/json/v3/ip | python3 -c 'import json,sys;print(json.load(sys.stdin)["yourIp"])')}
BH_ACME_EMAIL=${BH_ACME_EMAIL:-admin@$BH_DOMAIN}
BH_PG_PASSWORD=$(openssl rand -hex 24)
ENV
  chmod 600 .env
fi

docker compose up -d --build --remove-orphans
docker compose ps
