#!/usr/bin/env bash
# Nightly: every tool database, every tool volume, and the control-plane state,
# as one dated archive, shipped to object storage. Keeps 30 days remotely.
set -euo pipefail
cd /srv/boathouse
STATE=${BH_STATE_HOST:-/var/lib/boathouse/state}
DAY=$(date -u +%F)
OUT=/var/lib/boathouse/backups/$DAY; mkdir -p "$OUT"
docker exec bh-postgres pg_dumpall -U postgres | gzip > "$OUT/postgres.sql.gz"
# one restorable file per tool database (bh restore reads these): drops and recreates every object it holds
for d in $(docker exec bh-postgres psql -U postgres -Atc "select datname from pg_database where datname like 't\_%'"); do
  docker exec bh-postgres pg_dump -U postgres --clean --if-exists --no-owner --no-privileges "$d" | gzip > "$OUT/$d.sql.gz"
done
for v in $(docker volume ls -q --filter label=boathouse.tool); do
  docker run --rm -v "$v:/v:ro" -v "$OUT:/out" alpine tar czf "/out/$v.tgz" -C /v .
done
tar czf "$OUT/control-state.tgz" -C "$(dirname "$STATE")" "$(basename "$STATE")"
if [ -f /etc/boathouse/backup.env ]; then
  set -a; . /etc/boathouse/backup.env; set +a   # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BH_BACKUP_BUCKET, BH_BACKUP_ENDPOINT
  aws --endpoint-url "$BH_BACKUP_ENDPOINT" s3 sync "$OUT" "s3://$BH_BACKUP_BUCKET/boathouse/$DAY/" --only-show-errors
  [ -n "${HEALTHCHECK_URL_BACKUP:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_URL_BACKUP" >/dev/null || true
fi
find /var/lib/boathouse/backups -maxdepth 1 -mtime +7 -type d -exec rm -rf {} +
echo "backup $DAY ok"
