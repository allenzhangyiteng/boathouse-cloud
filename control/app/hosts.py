"""Which workspace, and which face of it, a hostname belongs to.

A workspace is reachable on its own domains (bought with `bh domain buy`, or
attached) and on its free address under the platform:

    finance.tools.example.com          tool "finance" on the custom domain
    auth.tools.example.com             sign-in for that domain (cookie scope .tools.example.com)
    tools.example.com                  the workspace home: the list of tools

    finance.starter.boathousecloud.com   the same tool on the free address
    auth.starter.boathousecloud.com
    starter.boathousecloud.com

    api.boathousecloud.com         the API the bh command talks to
    mcp.boathousecloud.com         the same API as MCP tools, for agents that speak MCP
    boathousecloud.com             the platform itself
"""
from dataclasses import dataclass

from . import config, db

RESERVED = {"auth", "www", "api", "tools", "boathouse", "control", "postgres", "caddy", "mail", "ns1", "ns2"}


@dataclass
class Where:
    kind: str                 # platform | api | mcp | home | auth | tool | unknown
    base: str = ""            # the domain the cookie is scoped to
    workspace: object = None  # workspaces row
    slug: str = ""            # tool slug when kind == tool

    @property
    def auth_host(self) -> str:
        return f"auth.{self.base}"

    @property
    def cookie_domain(self) -> str:
        return f".{self.base}"


def workspace_domains(workspace_id: str) -> list:
    with db.conn() as c:
        return c.execute("SELECT * FROM domains WHERE workspace_id=? AND status='active' ORDER BY is_primary DESC, created",
                         (workspace_id,)).fetchall()


def base_for(ws) -> str:
    """The address tools are announced on: the primary custom domain, else the free one."""
    doms = workspace_domains(ws["id"])
    return doms[0]["domain"] if doms else f"{ws['slug']}.{config.PLATFORM_DOMAIN}"


def all_bases(ws) -> list[str]:
    return [d["domain"] for d in workspace_domains(ws["id"])] + [f"{ws['slug']}.{config.PLATFORM_DOMAIN}"]


def resolve(host: str) -> Where:
    host = (host or "").split(":")[0].lower().rstrip(".")
    plat = config.PLATFORM_DOMAIN
    if host == plat or host == f"www.{plat}":
        return Where("platform", plat)
    if host == config.API_HOST:
        return Where("api", plat)
    if host == config.MCP_HOST:
        return Where("mcp", plat)
    # Prefer a workspace's most specific address. Never serve a legacy overlap
    # across workspaces: their ancestor-scoped login cookies share a namespace.
    with db.conn() as c:
        matches = [d for d in c.execute("SELECT * FROM domains WHERE status='active' ORDER BY LENGTH(domain) DESC")
                   if host == d["domain"] or host.endswith("." + d["domain"])]
        if len({d["workspace_id"] for d in matches}) > 1:
            return Where("unknown", host)
        for d in matches:
            dom = d["domain"]
            if host == dom or host.endswith("." + dom):
                ws = db.workspace_by_id(d["workspace_id"])
                front = d["tool_slug"] if "tool_slug" in d.keys() else None
                if front and host in (dom, "www." + dom):
                    return Where("tool", dom, ws, front)      # the domain's front door is a tool: apex and www both
                return _face(host, dom, ws)
    # free address: <x>.<ws>.<platform> or <ws>.<platform>
    if host.endswith("." + plat):
        rest = host[: -len(plat) - 1].split(".")
        ws = db.workspace(rest[-1])
        if ws and 1 <= len(rest) <= 2:
            return _face(host, f"{ws['slug']}.{plat}", ws)
    return Where("unknown", host)


def _face(host: str, base: str, ws) -> Where:
    if host == base:
        return Where("home", base, ws)
    label = host[: -len(base) - 1]
    if "." in label:
        return Where("unknown", base, ws)
    if label == "auth":
        return Where("auth", base, ws)
    return Where("tool", base, ws, label)


def tool_for(where: Where):
    if where.kind != "tool" or not where.workspace:
        return None
    with db.conn() as c:
        return c.execute("SELECT * FROM tools WHERE workspace_id=? AND slug=?", (where.workspace["id"], where.slug)).fetchone()


def front_domain(workspace_id: str, slug: str):
    """The custom domain whose front (apex and www) is this tool, if any."""
    with db.conn() as c:
        return c.execute("SELECT domain FROM domains WHERE workspace_id=? AND tool_slug=? AND status='active' ORDER BY is_primary DESC LIMIT 1",
                         (workspace_id, slug)).fetchone()


def tool_url(ws, slug: str) -> str:
    d = front_domain(ws["id"], slug)
    return f"https://{d['domain']}" if d else f"https://{slug}.{base_for(ws)}"
