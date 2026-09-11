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
# Keep the last completed local snapshot while writing the next one. Older
# legacy backups without our completion marker are left for operator review.
python3 - <<'PRUNE'
from pathlib import Path
import re,shutil
root=Path('/var/lib/boathouse/backups')
complete=sorted([p for p in root.glob('20??-??-??') if p.is_dir() and not p.is_symlink() and re.fullmatch(r'20\d{2}-\d{2}-\d{2}',p.name) and (p/'.complete').is_file()])
for p in complete[:-1]:shutil.rmtree(p)
PRUNE
OUT=/var/lib/boathouse/backups/$DAY; mkdir -p "$OUT"
# Refuse a backup that could consume the host's emergency free-space reserve.
python3 - <<'SPACE'
import json,shutil,subprocess
from pathlib import Path
root=Path('/var/lib/boathouse')
allocation=root/'guard/allocations.sqlite3'
if allocation.exists():
 import sqlite3
 with sqlite3.connect('file:'+str(allocation)+'?mode=ro',uri=True) as c:
  data=c.execute("SELECT coalesce(sum(limit_bytes),0) FROM allocations WHERE kind='data'").fetchone()[0]
 state=int(subprocess.check_output(['du','-sk',str(root/'state')],text=True).split()[0])*1024
 required=int((data+state)*1.15)+8*1024**3
 if shutil.disk_usage(root).free<required:raise SystemExit('Backup needs operator review: insufficient temporary disk reserve. Existing snapshots and app data are preserved.')
SPACE
touch "$OUT/.inprogress"
rm -f "$OUT/.complete"
chmod 700 /var/lib/boathouse/backups "$OUT"
docker exec bh-postgres pg_dumpall -U postgres --globals-only | gzip > "$OUT/postgres.sql.gz.tmp"
mv "$OUT/postgres.sql.gz.tmp" "$OUT/postgres.sql.gz"
# one restorable file per tool database (bh restore reads these): drops and recreates every object it holds
for d in $(docker exec bh-postgres psql -U postgres -Atc "select datname from pg_database where datname like 't\_%'"); do
  docker exec bh-postgres pg_dump -U postgres --clean --if-exists --no-owner --no-privileges --no-tablespaces "$d" | gzip > "$OUT/$d.sql.gz.tmp"
  mv "$OUT/$d.sql.gz.tmp" "$OUT/$d.sql.gz"
done
for v in $(docker volume ls -q --filter label=boathouse.tool); do
  # Migration keeps old data volumes for rollback. Back up only the active copy.
  if [[ "$v" != *-bounded-data ]] && docker volume inspect "${v%-data}-bounded-data" >/dev/null 2>&1; then continue; fi
  docker run --rm --memory 128m --memory-swap 128m --cpus .5 --pids-limit 64 -v "$v:/v:ro" -v "$OUT:/out" alpine sh -c 'umask 077; tar czf "/out/$1.tgz.tmp" -C /v .' sh "$v"
  mv "$OUT/$v.tgz.tmp" "$OUT/$v.tgz"
done
# SQLite's online backup API includes committed WAL data consistently. Copying
# a live SQLite file with tar can miss transactions or produce a torn snapshot.
python3 /srv/boathouse/deploy/snapshot-state.py "$STATE" "$OUT/control-state.tgz"
find "$OUT" -type f -exec chmod 600 {} +
if [ -f /etc/boathouse/backup.env ]; then
  set -a; . /etc/boathouse/backup.env; set +a   # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BH_BACKUP_BUCKET, BH_BACKUP_ENDPOINT
  aws --endpoint-url "$BH_BACKUP_ENDPOINT" s3 sync "$OUT" "s3://$BH_BACKUP_BUCKET/boathouse/$DAY/" --only-show-errors
  # Retention is restricted to dated Boat House prefixes in the shared bucket.
  cutoff=$(date -u -d '29 days ago' +%F)
  prefixes=$(aws --endpoint-url "$BH_BACKUP_ENDPOINT" s3 ls "s3://$BH_BACKUP_BUCKET/boathouse/" | awk '$1 == "PRE" {print $2}')
  while read -r prefix; do
    if [[ "$prefix" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}/$ && "${prefix%/}" < "$cutoff" ]]; then
      aws --endpoint-url "$BH_BACKUP_ENDPOINT" s3 rm "s3://$BH_BACKUP_BUCKET/boathouse/$prefix" --recursive --only-show-errors
    fi
  done <<< "$prefixes"
  [ -n "${HEALTHCHECK_URL_BACKUP:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_URL_BACKUP" >/dev/null || true
fi
rm -f "$OUT/.inprogress"
touch "$OUT/.complete"
echo "backup $DAY ok"
