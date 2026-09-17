"""SQLite store for the control plane. One file, a handful of tables, every row
carries a workspace id so a second tenant is a row, not a rewrite."""
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, name TEXT,
  role TEXT NOT NULL CHECK(role IN ('owner','member','guest')),
  created REAL NOT NULL, created_by TEXT, UNIQUE(workspace_id, email));
CREATE TABLE IF NOT EXISTS tools (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, slug TEXT NOT NULL, name TEXT NOT NULL,
  default_access TEXT NOT NULL DEFAULT 'members' CHECK(default_access IN ('members','listed')),
  default_role TEXT NOT NULL DEFAULT 'member',
  current_release_id TEXT, signing_key TEXT NOT NULL, db_password TEXT NOT NULL,
  created REAL NOT NULL, created_by TEXT, UNIQUE(workspace_id, slug));
CREATE TABLE IF NOT EXISTS claims (
  code TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, key_enc TEXT NOT NULL,
  created REAL NOT NULL, expires REAL NOT NULL, used REAL);
CREATE TABLE IF NOT EXISTS access_requests (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, tool_id TEXT NOT NULL, email TEXT NOT NULL, tier TEXT NOT NULL,
  message TEXT, token_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
  decided REAL, decision TEXT, decided_by TEXT);
CREATE TABLE IF NOT EXISTS grants (
  id TEXT PRIMARY KEY, tool_id TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL,
  created REAL NOT NULL, created_by TEXT, UNIQUE(tool_id, email));
CREATE TABLE IF NOT EXISTS releases (
  id TEXT PRIMARY KEY, tool_id TEXT NOT NULL, seq INTEGER NOT NULL, image TEXT NOT NULL,
  status TEXT NOT NULL, note TEXT, log TEXT, created REAL NOT NULL, created_by TEXT);
CREATE TABLE IF NOT EXISTS secrets (
  tool_id TEXT NOT NULL, name TEXT NOT NULL, value_enc TEXT NOT NULL,
  updated REAL NOT NULL, updated_by TEXT, PRIMARY KEY(tool_id, name));
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, name TEXT,
  created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_keys (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, name TEXT NOT NULL,
  key_hash TEXT UNIQUE NOT NULL, created REAL NOT NULL, last_used REAL);
CREATE TABLE IF NOT EXISTS accounts (
  email TEXT PRIMARY KEY, pw_hash TEXT, name TEXT, created REAL NOT NULL, last_login REAL);
CREATE TABLE IF NOT EXISTS invites (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
  created REAL NOT NULL, expires REAL NOT NULL, created_by TEXT, used REAL);
CREATE TABLE IF NOT EXISTS domains (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, domain TEXT UNIQUE NOT NULL,
  registrar TEXT NOT NULL, status TEXT NOT NULL, is_primary INTEGER NOT NULL DEFAULT 0,
  dns_ok INTEGER NOT NULL DEFAULT 0, cost_cents INTEGER, order_id TEXT,
  created REAL NOT NULL, created_by TEXT);
CREATE TABLE IF NOT EXISTS domain_allocations (
  domain TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, created REAL NOT NULL);
INSERT OR IGNORE INTO domain_allocations (domain,workspace_id,created)
  SELECT domain,workspace_id,created FROM domains WHERE registrar='porkbun';
CREATE TABLE IF NOT EXISTS domain_operations (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, domain TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('quoted','processing','uncertain','purchased','failed')),
  registrar_cents INTEGER NOT NULL, margin_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL,
  renewal_cents INTEGER NOT NULL, sandbox INTEGER NOT NULL, provider_fingerprint TEXT NOT NULL,
  created REAL NOT NULL, expires REAL NOT NULL, created_by TEXT NOT NULL,
  started REAL, lease_until REAL, settled REAL, provider_result TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS domain_operations_workspace ON domain_operations(workspace_id,domain,created);
CREATE UNIQUE INDEX IF NOT EXISTS domain_operations_active ON domain_operations(domain)
  WHERE state IN ('processing','uncertain','purchased');
CREATE TABLE IF NOT EXISTS domain_renewal_settings (
  domain TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
  enabled INTEGER NOT NULL, max_cost_cents INTEGER NOT NULL,
  expires_at REAL NOT NULL DEFAULT 0, managed INTEGER NOT NULL DEFAULT 0,
  provider_fingerprint TEXT NOT NULL, sandbox INTEGER NOT NULL,
  checked_at REAL, error TEXT, approved_by TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS domain_renewals (
  id TEXT PRIMARY KEY, domain TEXT NOT NULL, workspace_id TEXT NOT NULL,
  expires_at REAL NOT NULL, registrar_cents INTEGER NOT NULL, margin_cents INTEGER NOT NULL,
  total_cents INTEGER NOT NULL, years INTEGER NOT NULL, created REAL NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('waiting','processing','uncertain','renewed','failed','cancelled')),
  provider_fingerprint TEXT NOT NULL, sandbox INTEGER NOT NULL,
  started REAL, lease_until REAL, provider_result TEXT, error TEXT, settled REAL);
CREATE UNIQUE INDEX IF NOT EXISTS domain_renewal_cycle ON domain_renewals(domain,expires_at)
  WHERE state NOT IN ('failed','cancelled');
CREATE TABLE IF NOT EXISTS domain_renewal_notices (
  notice_key TEXT NOT NULL, email TEXT NOT NULL, domain TEXT NOT NULL,
  workspace_id TEXT, kind TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  created REAL NOT NULL, sent_at REAL, lease_until REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(notice_key,email));
CREATE TABLE IF NOT EXISTS domain_renewal_worker (
  id INTEGER PRIMARY KEY CHECK(id=1), lease_until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS referral_codes (
  code TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS referral_earnings (
  id TEXT PRIMARY KEY, referrer_email TEXT NOT NULL, workspace_id TEXT NOT NULL, cents INTEGER NOT NULL,
  ledger_ref TEXT UNIQUE NOT NULL, created REAL NOT NULL, paid_at REAL, payout_ref TEXT);
CREATE INDEX IF NOT EXISTS referral_earnings_by ON referral_earnings(referrer_email, paid_at);
CREATE TABLE IF NOT EXISTS referral_customers (
  email TEXT PRIMARY KEY, referrer_email TEXT NOT NULL, code TEXT NOT NULL,
  created REAL NOT NULL, discount_until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS referral_program (
  id INTEGER PRIMARY KEY CHECK(id=1), start_rowid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS referral_revenue (
  workspace_id TEXT PRIMARY KEY, referrer_email TEXT NOT NULL,
  basis_cents INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS referral_payouts (
  id TEXT PRIMARY KEY, email TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents>0),
  status TEXT NOT NULL CHECK(status IN ('prepared','paid','cancelled')),
  created REAL NOT NULL, created_by TEXT NOT NULL, paid_at REAL, paid_by TEXT,
  payout_ref TEXT UNIQUE);
CREATE UNIQUE INDEX IF NOT EXISTS referral_payout_pending ON referral_payouts(email) WHERE status='prepared';
CREATE TABLE IF NOT EXISTS payment_returns (
  provider_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
  refunded_cents INTEGER NOT NULL DEFAULT 0, disputed_cents INTEGER NOT NULL DEFAULT 0,
  held_cents INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ledger (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
  amount_cents INTEGER NOT NULL, balance_after INTEGER NOT NULL, memo TEXT, ref TEXT UNIQUE, created_by TEXT);
CREATE INDEX IF NOT EXISTS ledger_ws ON ledger(workspace_id, ts);
CREATE TABLE IF NOT EXISTS payment_operations (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, kind TEXT NOT NULL,
  cents INTEGER NOT NULL CHECK(cents>0), status TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL,
  created_by TEXT, month TEXT, customer TEXT, payment_method TEXT,
  provider_id TEXT UNIQUE, submitted REAL, error TEXT, payload_json TEXT);
CREATE INDEX IF NOT EXISTS payment_operations_ws ON payment_operations(workspace_id, status);
CREATE TABLE IF NOT EXISTS storage_accrual (
  workspace_id TEXT NOT NULL, resource_key TEXT NOT NULL, month TEXT NOT NULL,
  byte_cent_days INTEGER NOT NULL DEFAULT 0, charged_cents INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(workspace_id, resource_key, month));
CREATE TABLE IF NOT EXISTS resource_limits (
  resource_key TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, slug TEXT NOT NULL,
  limit_bytes INTEGER NOT NULL DEFAULT 1073741824, approved_by TEXT, approved_at REAL,
  sampled_at REAL, storage_bytes INTEGER, memory_bytes INTEGER, cpu_percent REAL,
  state TEXT NOT NULL DEFAULT 'ok', reason TEXT, high_cpu_since REAL,
  last_warning INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS resource_quotes (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, resource_key TEXT NOT NULL,
  old_limit_bytes INTEGER NOT NULL, limit_bytes INTEGER NOT NULL,
  max_extra_monthly_cents INTEGER NOT NULL, created_by TEXT NOT NULL,
  created REAL NOT NULL, expires REAL NOT NULL, confirmed REAL);
CREATE TABLE IF NOT EXISTS resource_notifications (
  id TEXT PRIMARY KEY, resource_key TEXT NOT NULL, level INTEGER NOT NULL,
  email TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  created REAL NOT NULL, sent REAL, last_attempt REAL,
  UNIQUE(resource_key,level,email,created));
CREATE TABLE IF NOT EXISTS settings (
  name TEXT PRIMARY KEY, value_enc TEXT NOT NULL, updated REAL NOT NULL, updated_by TEXT);
CREATE TABLE IF NOT EXISTS used_tokens (
  hash TEXT PRIMARY KEY, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tool_keepsakes (
  workspace_id TEXT NOT NULL, slug TEXT NOT NULL, grants TEXT NOT NULL, secrets TEXT NOT NULL, saved REAL NOT NULL,
  PRIMARY KEY(workspace_id, slug));
CREATE TABLE IF NOT EXISTS audit (
  ts REAL NOT NULL, workspace_id TEXT, actor TEXT, action TEXT NOT NULL, target TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
"""

# Columns added after the first release. (table, column, ddl)
MIGRATIONS = [
    ("payment_operations", "checkout_id", "ALTER TABLE payment_operations ADD COLUMN checkout_id TEXT"),
    ("payment_operations", "checkout_payload", "ALTER TABLE payment_operations ADD COLUMN checkout_payload TEXT"),
    ("users", "pw_hash", "ALTER TABLE users ADD COLUMN pw_hash TEXT"),
    ("users", "last_login", "ALTER TABLE users ADD COLUMN last_login REAL"),
    # tiers (2026-09-07): a grant is a tier (viewer|editor|admin) plus optional app labels
    ("grants", "tier", "ALTER TABLE grants ADD COLUMN tier TEXT"),
    ("grants", "labels", "ALTER TABLE grants ADD COLUMN labels TEXT NOT NULL DEFAULT ''"),
    ("tools", "default_tier", "ALTER TABLE tools ADD COLUMN default_tier TEXT NOT NULL DEFAULT 'viewer'"),
    # hosted source: each release keeps the folder it was built from
    ("releases", "source", "ALTER TABLE releases ADD COLUMN source TEXT"),
    # money (phase B)
    ("workspaces", "balance_cents", "ALTER TABLE workspaces ADD COLUMN balance_cents INTEGER NOT NULL DEFAULT 0"),
    ("workspaces", "paused", "ALTER TABLE workspaces ADD COLUMN paused INTEGER NOT NULL DEFAULT 0"),
    ("workspaces", "autorefill_cents", "ALTER TABLE workspaces ADD COLUMN autorefill_cents INTEGER NOT NULL DEFAULT 0"),
    ("workspaces", "autorefill_cap_cents", "ALTER TABLE workspaces ADD COLUMN autorefill_cap_cents INTEGER NOT NULL DEFAULT 0"),
    ("workspaces", "stripe_customer", "ALTER TABLE workspaces ADD COLUMN stripe_customer TEXT"),
    ("workspaces", "stripe_pm", "ALTER TABLE workspaces ADD COLUMN stripe_pm TEXT"),
    ("workspaces", "owner_email", "ALTER TABLE workspaces ADD COLUMN owner_email TEXT"),
    # 2026-09-08: a tool can be public (anyone may read it), and a domain can put a tool on its front (apex + www)
    ("tools", "public", "ALTER TABLE tools ADD COLUMN public INTEGER NOT NULL DEFAULT 0"),
    ("domains", "tool_slug", "ALTER TABLE domains ADD COLUMN tool_slug TEXT"),
    # 2026-09-09: referrals. No welcome credit any more; a code at signup halves the tool rate for sixty days
    ("workspaces", "referred_by", "ALTER TABLE workspaces ADD COLUMN referred_by TEXT"),
    ("workspaces", "discount_until", "ALTER TABLE workspaces ADD COLUMN discount_until REAL"),
    ("tools", "resource_key", "ALTER TABLE tools ADD COLUMN resource_key TEXT"),
    ("tool_keepsakes", "resource_key", "ALTER TABLE tool_keepsakes ADD COLUMN resource_key TEXT"),
    ("tool_keepsakes", "db_password", "ALTER TABLE tool_keepsakes ADD COLUMN db_password TEXT"),
    ("tool_keepsakes", "signing_key", "ALTER TABLE tool_keepsakes ADD COLUMN signing_key TEXT"),
]

TIERS = ("viewer", "editor", "admin")


def resource_migration_plan(c=None):
    """Read-only legacy ownership report. Contains identifiers, never credentials.

    Run before upgrading: duplicate legacy names must be investigated, not guessed.
    Both live tools and retained data have ownership claims on the old slug names.
    """
    if c is None:
        with sqlite3.connect(config.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            return resource_migration_plan(connection)
    claims = {}
    pending = []
    for table in ("tools", "tool_keepsakes"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue
        key = "resource_key" if "resource_key" in cols else "NULL AS resource_key"
        for row in c.execute(f"SELECT workspace_id, slug, {key} FROM {table}"):
            resource = row["resource_key"] or row["slug"]
            owner = (row["workspace_id"], row["slug"])
            claims.setdefault(resource, set()).add(owner)
            if not row["resource_key"]:
                pending.append({"table": table, "workspace_id": row["workspace_id"], "slug": row["slug"], "resource_key": resource})
    conflicts = [{"resource_key": key, "owners": [{"workspace_id": wid, "slug": slug} for wid, slug in sorted(owners)]}
                 for key, owners in sorted(claims.items()) if len(owners) > 1]
    return {"safe": not conflicts, "legacy_assignments": pending, "conflicts": conflicts}


def _migrate_resources(c):
    plan = resource_migration_plan(c)
    if not plan["safe"]:
        raise RuntimeError("resource isolation migration stopped: legacy resources have multiple workspace owners; "
                           "inspect db.resource_migration_plan() and resolve ownership before upgrading: " +
                           json.dumps(plan["conflicts"]))
    for row in plan["legacy_assignments"]:
        c.execute(f"UPDATE {row['table']} SET resource_key=? WHERE workspace_id=? AND slug=?",
                  (row["resource_key"], row["workspace_id"], row["slug"]))
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS tools_resource_key ON tools(resource_key)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS keepsakes_resource_key ON tool_keepsakes(resource_key)")
    # Inserts that omit the new column (including operator scripts) still get an
    # opaque, globally unique name. Legacy slug names are assigned only above.
    c.execute("""CREATE TRIGGER IF NOT EXISTS tools_resource_key_default AFTER INSERT ON tools
                 WHEN NEW.resource_key IS NULL BEGIN
                 UPDATE tools SET resource_key='r-' || lower(hex(randomblob(16))) WHERE id=NEW.id; END""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS tools_resource_key_immutable BEFORE UPDATE OF resource_key ON tools
                 WHEN OLD.resource_key IS NOT NULL AND NEW.resource_key IS NOT OLD.resource_key
                 BEGIN SELECT RAISE(ABORT, 'tool resource identity is immutable'); END""")


def _backfill(c):
    """One-time data moves after a column appears."""
    # one password per person, kept in accounts; the first workspace row with a password wins
    for u in c.execute("SELECT email, pw_hash, name, created, last_login FROM users WHERE pw_hash IS NOT NULL ORDER BY created").fetchall():
        c.execute("""INSERT INTO accounts (email, pw_hash, name, created, last_login) VALUES (?,?,?,?,?)
                     ON CONFLICT(email) DO NOTHING""", (u["email"], u["pw_hash"], u["name"], u["created"], u["last_login"]))
    for g in c.execute("SELECT id, role FROM grants WHERE tier IS NULL").fetchall():
        role = (g["role"] or "viewer").lower()
        if role in ("owner", "admin"):
            tier, labels = "admin", ""
        elif role in ("member", "editor"):
            tier, labels = "editor", ""
        elif role == "viewer":
            tier, labels = "viewer", ""
        else:
            tier, labels = "viewer", role          # e.g. consultant: a label on a viewer
        c.execute("UPDATE grants SET tier=?, labels=? WHERE id=?", (tier, labels, g["id"]))


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@contextmanager
def conn():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
    finally:
        c.close()


def init():
    with conn() as c:
        # SQLite DDL is transactional. A conflicting legacy owner leaves every
        # migration and backfill unchanged so the old release can keep running.
        c.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        for table, col, ddl in MIGRATIONS:
            cols = [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                c.execute(ddl)
        _migrate_resources(c)
        _backfill(c)
        # Preserve old accrued/paid commissions. The new engine only adds new
        # usage when an installation already has historical earnings.
        legacy = c.execute("SELECT 1 FROM referral_earnings LIMIT 1").fetchone()
        boundary = c.execute("SELECT COALESCE(MAX(rowid),0) FROM ledger").fetchone()[0] if legacy else 0
        c.execute("INSERT OR IGNORE INTO referral_program VALUES(1,?)", (boundary,))
        for r in c.execute("SELECT w.owner_email,w.referred_by,w.created,w.discount_until,r.code "
                           "FROM workspaces w JOIN referral_codes r ON r.email=w.referred_by "
                           "WHERE w.owner_email IS NOT NULL ORDER BY w.created,w.id").fetchall():
            if r['owner_email'].lower() != r['referred_by'].lower():
                c.execute("INSERT OR IGNORE INTO referral_customers VALUES(?,?,?,?,?)",
                          (r['owner_email'].lower(),r['referred_by'].lower(),r['code'],r['created'],r['discount_until'] or r['created']))
        ws = c.execute("SELECT id FROM workspaces WHERE slug=?", (config.WORKSPACE_SLUG,)).fetchone()
        if not ws:
            wid = new_id("ws")
            c.execute("INSERT INTO workspaces (id, slug, name, created) VALUES (?,?,?,?)",
                      (wid, config.WORKSPACE_SLUG, config.WORKSPACE_NAME, time.time()))
        else:
            wid = ws["id"]
        for email in config.BOOTSTRAP_OWNERS:
            c.execute("""INSERT INTO users (id, workspace_id, email, name, role, created, created_by)
                         VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace_id, email) DO UPDATE SET role='owner'""",
                      (new_id("u"), wid, email, None, "owner", time.time(), "bootstrap"))
        c.execute("COMMIT")
        return wid


def workspace(slug: str | None = None):
    with conn() as c:
        return c.execute("SELECT * FROM workspaces WHERE slug=?", (slug or config.WORKSPACE_SLUG,)).fetchone()


def workspace_by_id(wid: str):
    with conn() as c:
        return c.execute("SELECT * FROM workspaces WHERE id=?", (wid,)).fetchone()


def audit(actor, action, target=None, detail=None, workspace_id=None):
    with conn() as c:
        c.execute("INSERT INTO audit VALUES (?,?,?,?,?,?)",
                  (time.time(), workspace_id or workspace()["id"], actor, action, target,
                   json.dumps(detail) if detail is not None else None))
