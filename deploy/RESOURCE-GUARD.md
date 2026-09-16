# Resource guard operations

The optional guard is intended for a small single-host installation. Enable it
only after backing up and moving each app's database and data volume into its
own XFS project. Enabling the compose override on legacy volumes without that
migration is unsupported and intentionally refuses deployment.

The broker reserves allocation capacity before approving growth. The default
20 GiB pool reserves 20% free space and 3 GiB for one build. A separate 8 GiB host
reserve and an eight-running-app ceiling prevent automatic oversubscription.
These are admission limits, not a promise of eight simultaneous heavy workloads.
Operators must review host capacity before raising them. CPU, memory, process,
log, source archive, database connection, temporary query, image and build limits
also apply; app root filesystems are read-only.

`resource-agent.py` runs as root on a mode-0600 Unix socket, mounted only into the
trusted control service. Application and rootless builder containers block the
ioctls that could change XFS project IDs or inheritance. Never run customer code
with a privileged container, an unconfined app syscall profile, the broker socket,
or a Docker socket. Use the pinned builder image and explicit bounded cache
mounts: its declared cache volume must never become an anonymous Docker volume.

Storage warnings occur at 80% and 90%. At the limit the kernel refuses writes;
the monitor pauses the affected app at 99.5%. Owner-approved capacity increases
or cleanup below 90% allow resume, subject to account credit and host capacity.
Memory and sustained CPU warnings are advisory; their container ceilings still
apply. Monitor failures must be investigated; native storage caps remain active.

Nightly backups keep one completed local snapshot while writing the next and
30 dated remote Boat House prefixes. Backups refuse to exhaust the host reserve.
Legacy local directories without a completion marker are deliberately preserved
for operator review. The object-storage retention code only touches the
`boathouse/YYYY-MM-DD/` prefix, never unrelated application backups.

Validate a rollout with a disposable app: build, serve, write data, exceed its
quota, attempt project-ID changes, approve capacity, resume, export, and restore.
Also verify every migrated database dump, preserve original volumes and stopped
containers for rollback, and check the first new backup before removing those
rollback copies. Do not prune unknown Docker volumes or customer state.

Hosted manual top-ups use Stripe Checkout. The return page retrieves the Stripe
session and PaymentIntent; a return URL alone cannot grant credit. Replayed
callbacks share a durable operation and ledger reference. Auto-refill is an
explicit opt-in with a monthly cap. Use Stripe test mode and a separate ledger
for success, decline, authentication and replay testing; never replace live
production Stripe keys with test keys.


## Organization plan

Managed pricing is one $10 calendar-month charge per organization, including up
to five tools. Static and stopped tools count toward this limit. New organization
debits use `organization-meter:` references; partner commissions recognize both
new and legacy references. A legacy debit on the rollout day prevents another
organization charge that day. Historical charges and balances are not rewritten.

With the guard enabled, all app containers in an organization must use its
broker-issued systemd slice. It enforces 512 MiB memory, zero swap, 50% CPU and
1,024 tasks in aggregate. Per-container limits still apply within the pool.
The host must use systemd and cgroup v2. Install the updated broker before the
control service and recreate existing containers into their new parent one at a
time. Slice unit files persist across reboot. Do not advertise pooled limits
on hosts where the broker is disabled.

The conservative default remains eight running apps. A measured light-workload
mix can support a configured limit of 30 tools across six active organizations
on the managed 4-vCPU, 8-GB host. This is an admission ceiling, not a guarantee
for arbitrary workloads. The quota filesystem must reserve all data allocations
plus the build allowance; a 20-GiB pool is insufficient for 30 one-GiB tools.
Reassess storage, database connections, backups, CPU and memory before increasing
`BH_MAX_RUNNING_TOOLS` or `BH_MAX_ACTIVE_ORGANIZATIONS` on another host.
