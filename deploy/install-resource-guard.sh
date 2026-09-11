#!/usr/bin/env bash
# One-time host preparation. Existing data is migrated separately after backup.
set -euo pipefail
ROOT=/var/lib/boathouse/storage
IMAGE=/var/lib/boathouse/storage.xfs
mkdir -p "$ROOT" /var/lib/boathouse/guard /run/boathouse-guard
chmod 700 /var/lib/boathouse/guard /run/boathouse-guard
if ! mountpoint -q "$ROOT"; then
  if [ ! -f "$IMAGE" ]; then
    [ "$(df --output=avail -B1 /var/lib/boathouse | tail -1)" -gt 32212254720 ] || { echo 'Insufficient reserved host disk'; exit 1; }
    fallocate -l 20G "$IMAGE"
    chmod 600 "$IMAGE"
    mkfs.xfs -f -m reflink=1 -n ftype=1 "$IMAGE" >/dev/null
  fi
  mount -o loop,pquota "$IMAGE" "$ROOT"
fi
python3 - <<'PY'
from pathlib import Path
p=Path('/etc/fstab');s=p.read_text()
line='/var/lib/boathouse/storage.xfs /var/lib/boathouse/storage xfs loop,pquota 0 0'
if line not in s:p.write_text(s.rstrip()+'\n'+line+'\n')
PY
install -m 755 /srv/boathouse/deploy/resource-agent.py /usr/local/lib/boathouse-resource-agent.py
cat > /etc/systemd/system/boathouse-resource-guard.service <<'UNIT'
[Unit]
Description=Boat House resource quota broker
RequiresMountsFor=/var/lib/boathouse/storage
After=local-fs.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/lib/boathouse-resource-agent.py
Restart=on-failure
RestartSec=2
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/boathouse-storage.conf <<'UNIT'
[Unit]
RequiresMountsFor=/var/lib/boathouse/storage
UNIT
docker network inspect bh-build >/dev/null 2>&1 || docker network create --subnet 10.201.0.0/24 bh-build >/dev/null
cat > /usr/local/lib/boathouse-build-firewall <<'FIREWALL'
#!/usr/bin/env bash
set -euo pipefail
# A build may download public packages. It cannot reach app databases, host
# services, cloud metadata or private networks. Return traffic is allowed.
iptables -N BH-BUILD 2>/dev/null || true
iptables -F BH-BUILD
iptables -A BH-BUILD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
for network in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
  iptables -A BH-BUILD -d "$network" -j REJECT
done
iptables -A BH-BUILD -j RETURN
iptables -C DOCKER-USER -s 10.201.0.0/24 -j BH-BUILD 2>/dev/null || iptables -I DOCKER-USER 1 -s 10.201.0.0/24 -j BH-BUILD
iptables -C INPUT -s 10.201.0.0/24 -j REJECT 2>/dev/null || iptables -I INPUT 1 -s 10.201.0.0/24 -j REJECT
FIREWALL
chmod 755 /usr/local/lib/boathouse-build-firewall
cat > /etc/systemd/system/boathouse-build-firewall.service <<'UNIT'
[Unit]
Description=Boat House build network isolation
After=docker.service
Requires=docker.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/lib/boathouse-build-firewall
[Install]
WantedBy=docker.service
UNIT
systemctl daemon-reload
systemctl enable --now boathouse-resource-guard boathouse-build-firewall
