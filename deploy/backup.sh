#!/usr/bin/env bash
# Nightly: every tool database, every tool volume, and the control-plane state,
# as one dated archive, shipped to object storage. Keeps 30 days remotely.
set -euo pipefail
umask 077
cd /srv/boathouse
exec 9>/var/lock/boathouse-backup.lock
flock -n 9 || { echo "another backup is running" >&2; exit 1; }
STATE=${BH_STATE_HOST:-/var/lib/boathouse/state}
DAY=$(date -u +%F)
OUT=/var/lib/boathouse/backups/$DAY; mkdir -p "$OUT"
chmod 700 /var/lib/boathouse/backups "$OUT"
docker exec bh-postgres pg_dumpall -U postgres | gzip > "$OUT/postgres.sql.gz.tmp"
mv "$OUT/postgres.sql.gz.tmp" "$OUT/postgres.sql.gz"
# one restorable file per tool database (bh restore reads these): drops and recreates every object it holds
for d in $(docker exec bh-postgres psql -U postgres -Atc "select datname from pg_database where datname like 't\_%'"); do
  docker exec bh-postgres pg_dump -U postgres --clean --if-exists --no-owner --no-privileges "$d" | gzip > "$OUT/$d.sql.gz.tmp"
  mv "$OUT/$d.sql.gz.tmp" "$OUT/$d.sql.gz"
done
for v in $(docker volume ls -q --filter label=boathouse.tool); do
  docker run --rm -v "$v:/v:ro" -v "$OUT:/out" alpine sh -c 'umask 077; tar czf "/out/$1.tgz.tmp" -C /v .' sh "$v"
  mv "$OUT/$v.tgz.tmp" "$OUT/$v.tgz"
done
# SQLite's online backup API includes committed WAL data consistently. Copying
# a live SQLite file with tar can miss transactions or produce a torn snapshot.
python3 /srv/boathouse/deploy/snapshot-state.py "$STATE" "$OUT/control-state.tgz"
find "$OUT" -type f -exec chmod 600 {} +
if [ -f /etc/boathouse/backup.env ]; then
  set -a; . /etc/boathouse/backup.env; set +a   # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BH_BACKUP_BUCKET, BH_BACKUP_ENDPOINT
  aws --endpoint-url "$BH_BACKUP_ENDPOINT" s3 sync "$OUT" "s3://$BH_BACKUP_BUCKET/boathouse/$DAY/" --only-show-errors
  [ -n "${HEALTHCHECK_URL_BACKUP:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_URL_BACKUP" >/dev/null || true
fi
find /var/lib/boathouse/backups -mindepth 1 -maxdepth 1 -name '20??-??-??' -mtime +7 -type d -exec rm -rf {} +
echo "backup $DAY ok"
