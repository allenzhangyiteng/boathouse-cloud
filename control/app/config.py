"""Boathouse control plane configuration. Everything comes from the environment
(the compose .env file on the box); nothing here is a secret by itself."""
import os
from pathlib import Path


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"missing required env {name}")
    return v


# The host's own domain (boathousecloud.com). The API lives at api.<platform>,
# and every workspace gets free addresses under <workspace>.<platform>.
PLATFORM_DOMAIN = _req("BH_DOMAIN").lower()
API_HOST = f"api.{PLATFORM_DOMAIN}"
MCP_HOST = f"mcp.{PLATFORM_DOMAIN}"
TRIAL_CREDIT_CENTS = int(os.environ.get("BH_TRIAL_CREDIT_CENTS", "0"))      # no welcome credit: a referral code is the discount
PUBLIC_IP = os.environ.get("BH_PUBLIC_IP", "")          # the box's address, for DNS records
ACME_EMAIL = os.environ.get("BH_ACME_EMAIL", f"admin@{PLATFORM_DOMAIN}")

SESSION_COOKIE = "bh_session"          # a tenant session, scoped to that workspace's domain
PLATFORM_COOKIE = "bh_account"         # the Boathouse account session, host-only on the platform apex
SESSION_DAYS = 365
INVITE_DAYS = 7
MIN_PASSWORD = 12

STATE_DIR = Path(os.environ.get("BH_STATE_DIR", "/state"))
DB_PATH = STATE_DIR / "boathouse.sqlite3"
BUILD_DIR = STATE_DIR / "builds"
SOURCE_DIR = STATE_DIR / "source"                # hosted source: <immutable tool id>/<seq>.tar.gz per release
BACKUP_DIR = Path(os.environ.get("BH_BACKUP_DIR", "/backups"))          # nightly backups, read-only in the container
BACKUP_DIR_HOST = os.environ.get("BH_BACKUP_HOST", "/var/lib/boathouse/backups")  # the same folder as the docker daemon sees it
MASTER_KEY_PATH = STATE_DIR / "master.key"       # fernet key for secrets + cookie signing

PG_HOST = os.environ.get("BH_PG_HOST", "postgres")
PG_ADMIN_PASSWORD = _req("BH_PG_PASSWORD")

# Resource ceiling per tool container. One runaway tool cannot starve the box.
TOOL_MEMORY = os.environ.get("BH_TOOL_MEMORY", "512m")
TOOL_CPUS = float(os.environ.get("BH_TOOL_CPUS", "1.0"))
TOOL_PIDS = int(os.environ.get("BH_TOOL_PIDS", "256"))
TOOL_PORT = 8080
KEEP_RELEASES = 5

# The first workspace, seeded on boot. More workspaces are rows, not config.
WORKSPACE_SLUG = os.environ.get("BH_WORKSPACE", "starter").lower()
WORKSPACE_NAME = os.environ.get("BH_WORKSPACE_NAME", "Starter")
# First owner(s), comma separated. Seeded on boot, never removed automatically.
BOOTSTRAP_OWNERS = [e.strip().lower() for e in os.environ.get("BH_OWNERS", "").split(",") if e.strip()]

# Registrar. Keys live encrypted in the settings table; these only pick the provider.
REGISTRAR = os.environ.get("BH_REGISTRAR", "porkbun")

# The host's own workspace: its owners run the registrar, the card processor, and may grant credit.
HOST_WORKSPACE = os.environ.get("BH_HOST_WORKSPACE", WORKSPACE_SLUG).lower()
METER = os.environ.get("BH_METER", "1") != "0"          # the hourly metering loop; off in tests
