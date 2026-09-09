#!/usr/bin/env bash
# Push this checkout to the box. Code only: the box's .env, state and backups
# live OUTSIDE the checkout (/srv/boathouse/.env, /var/lib/boathouse, /etc/boathouse)
# and are never touched. Never run a bare rsync --delete against /srv/boathouse.
set -euo pipefail
HOST=${1:?usage: deploy/sync.sh root@<box-ip> [--restart]}
cd "$(dirname "$0")/.."
rsync -az --delete --exclude .git --exclude __pycache__ --exclude .env --exclude '.env.*' --exclude state --exclude backups --exclude backup.env ./ "$HOST:/srv/boathouse/"
if [ "${2:-}" = "--restart" ]; then
  ssh "$HOST" 'cd /srv/boathouse && docker compose up -d --build --remove-orphans control caddy && docker exec bh-caddy caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -1 && sleep 3 && docker compose ps --format "{{.Name}} {{.Status}}"'
fi
