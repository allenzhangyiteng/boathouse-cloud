"""The MCP face of Boathouse, served at mcp.<platform> on POST /mcp.

Design: this module is a thin translator, not a second control plane. Every tool
here does its work by calling the same HTTP API the `bh` command calls
(http://127.0.0.1:8000 with Host: api.<platform>), passing the caller's own
Authorization header straight through. So there is exactly one source of truth
for permissions, validation, wording and money, and an agent speaking MCP can do
neither more nor less than a person at the terminal. Nothing is cached and no key
is ever stored: the project key lives in the client's header for the length of a
request.

Connecting a client
-------------------
    URL     https://mcp.boathousecloud.com/mcp      (Streamable HTTP, no SSE)
    Header  Authorization: Bearer <project key>     (from the account page, or bh keys create)

Claude Code:

    claude mcp add --transport http boathouse https://mcp.boathousecloud.com/mcp \
      --header "Authorization: Bearer bh_your_project_key"

Claude Desktop (claude_desktop_config.json):

    {"mcpServers": {"boathouse": {"type": "http",
       "url": "https://mcp.boathousecloud.com/mcp",
       "headers": {"Authorization": "Bearer bh_your_project_key"}}}}

Protocol: JSON-RPC 2.0, MCP 2025-03-26. initialize / notifications/initialized /
ping / tools/list / tools/call, single messages or a JSON array batch.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import tarfile
import time
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import config, deploy, hosts

router = APIRouter()

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "boathouse", "version": "0.3"}
INSTRUCTIONS = (
    "Boathouse is a small cloud: a workspace deploys folder-with-a-Dockerfile tools that become "
    "login-protected URLs (https://<tool>.<workspace domain>), shares them by email at three tiers "
    "(viewer, editor, admin), keeps their source, secrets, logs and releases, and buys domains. "
    "Every tool below acts as the workspace whose project key you send: set the header "
    "Authorization: Bearer <project key> on this MCP connection (minted on the workspace's account page, "
    "or with `bh keys create`). The server is stateless: no session id, no server-to-client stream. "
    "Anything that spends money (domain_buy, topup) quotes first and only acts with confirm true."
)
JSONRPC_HEADERS = {"MCP-Protocol-Version": PROTOCOL_VERSION}

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


# =============================================================================
# talking to the HTTP API
# =============================================================================

def client_factory() -> httpx.AsyncClient:
    """The HTTP client the tools use. Tests replace this with an ASGI-backed one."""
    base = os.environ.get("BH_INTERNAL_API", "http://127.0.0.1:8000")
    return httpx.AsyncClient(base_url=base, timeout=900.0)


class ApiError(Exception):
    """A 4xx/5xx from the Boathouse API, already turned into words."""

    def __init__(self, text: str, detail: Any = None):
        super().__init__(text)
        self.text = text
        self.detail = detail


def _detail_text(status: int, body: bytes) -> tuple[str, Any]:
    """The API answers {"detail": "..."} or {"detail": {"error": ..., "code": ...}}."""
    try:
        payload = json.loads(body)
    except ValueError:
        return f"{status}: {body.decode(errors='replace')[:1000]}", None
    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    if isinstance(detail, dict):
        return f"{status}: {detail.get('error') or json.dumps(detail)}", detail
    if isinstance(detail, list):  # FastAPI validation errors
        return f"{status}: {json.dumps(detail)[:1000]}", None
    return f"{status}: {detail}", None


class Api:
    """One request's worth of API access: the client plus the caller's key."""

    def __init__(self, client: httpx.AsyncClient, authorization: str, workspace: str | None = None):
        self.client = client
        self.headers = {"Host": config.API_HOST, "Authorization": authorization, "User-Agent": "boathouse-mcp/0.3"}
        if workspace:
            self.headers["Boathouse-Workspace"] = workspace

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        r = await self.client.request(method, path, headers=self.headers, **kw)
        if r.status_code >= 400:
            text, detail = _detail_text(r.status_code, r.content)
            raise ApiError(text, detail)
        return r

    async def get(self, path: str, **kw) -> httpx.Response:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw) -> httpx.Response:
        return await self.request("POST", path, **kw)

    async def put(self, path: str, **kw) -> httpx.Response:
        return await self.request("PUT", path, **kw)

    async def patch(self, path: str, **kw) -> httpx.Response:
        return await self.request("PATCH", path, **kw)

    async def delete(self, path: str, **kw) -> httpx.Response:
        return await self.request("DELETE", path, **kw)


# =============================================================================
# small formatting helpers, in the CLI's voice
# =============================================================================

def _money(cents: int | None) -> str:
    return "-" if cents is None else f"${cents / 100:,.2f}"


def _ago(ts: float | None) -> str:
    if not ts:
        return "-"
    d = time.time() - float(ts)
    for unit, s in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= s:
            return f"{int(d // s)}{unit} ago"
    return "just now"


def _invite_note(r: dict) -> str:
    if not r.get("invite_url"):
        if r.get("emailed"):
            return f"\nemailed {r['email']} the address and what to do. Nothing else to send."
        return ""
    days = r.get("invite_expires_days") or r.get("expires_days")
    if r.get("emailed"):
        return (f"\nemailed {r['email']} a secure invitation. "
                f"If it does not arrive, share this page so they can request another email:\n  {r['invite_url']}")
    return (f"\nShare this invitation page with {r['email']}; they can request a secure confirmation email there:\n  {r['invite_url']}" +
            ("" if r.get("mail_configured") else "\n(this Boathouse has no email set up, so nothing was sent)"))


def _need(args: dict, name: str) -> Any:
    v = args.get(name)
    if v is None or v == "":
        raise ApiError(f"{name} is required")
    return v


# =============================================================================
# the tools
# =============================================================================

async def t_whoami(api: Api, a: dict):
    j = (await api.get("/api/whoami")).json()
    memberships = (await api.get("/api/me")).json().get("workspaces", [])
    j["workspaces"] = memberships
    text = (f"{j['email']} ({j['role']}) in workspace {j['workspace']}; tools live at *.{j['domain']}; "
            f"balance {_money(j['balance_cents'])}" + (" — WORKSPACE PAUSED (top up to resume)" if j["paused"] else ""))
    if len(memberships) > 1:
        text += "\nYour workspaces: " + ", ".join(w["slug"] for w in memberships) + ". Set workspace on each tool call to select one."
    return text, j


async def t_list_tools(api: Api, a: dict):
    rows = (await api.get("/api/tools")).json()
    if not rows:
        chk = (await api.get("/api/billing/deploy-check")).json()
        hint = "" if chk.get("ok", True) else f" First, money: {chk['message']}"
        return ("no tools yet. Deploy a folder with a Dockerfile, or a website folder with an index.html, to make one."
                + hint), {"items": rows}
    lines = [f"{'TOOL':<16}{'STATE':<10}{'REL':<6}{'DEPLOYED':<12}{'ACCESS':<10}{'YOU':<8}URL"]
    for t in rows:
        rel = t["release"] or {}
        lines.append(f"{t['slug']:<16}{t['status']['state']:<10}{('#' + str(rel.get('seq'))) if rel else '-':<6}"
                     f"{_ago(rel.get('created')):<12}{('public' if t.get('public') else t['default_access']):<10}{t.get('my_tier') or '-':<8}{t['url']}")
    return "\n".join(lines), {"items": rows}


def _who(t: dict) -> str:
    if t.get("public"):
        return "anyone on the internet (public); changing it still needs sign-in"
    return "workspace members" if t["default_access"] == "members" else "listed people only"


async def t_get_tool(api: Api, a: dict):
    slug = _need(a, "tool")
    t = (await api.get(f"/api/tools/{slug}")).json()
    rel = t["release"] or {}
    who = _who(t)
    lines = [f"{t['slug']} ({t['name']}): {t['url']}",
             f"state {t['status']['state']}" + (f", release #{rel.get('seq')} deployed {_ago(rel.get('created'))} by {rel.get('created_by')}" if rel else ", never deployed"),
             f"visible to: {who}" + (f" (members get tier {t['default_tier']})" if t["default_access"] == "members" and not t.get("public") else ""),
             f"you are: {t.get('my_tier') or 'no access'}"]
    for g in t.get("grants") or []:
        lines.append(f"  shared with {g['email']}: {g.get('tier') or g.get('role')}" + (f" (labels {g['labels']})" if g.get("labels") else ""))
    return "\n".join(lines), t


def _pack(files: dict, binaries: dict) -> bytes:
    """A tar.gz of the folder the agent is holding in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, blob in sorted(list(files.items()) + list(binaries.items())):
            info = tarfile.TarInfo(path)
            info.size = len(blob)
            info.mtime = int(time.time())
            info.mode = 0o755 if path.endswith(".sh") else 0o644
            tf.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


def _safe_path(path: str) -> str:
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or ".." in p.split("/"):
        raise ApiError(f"refusing suspicious path: {path!r}. Paths are relative to the tool's folder.")
    return p


async def t_deploy(api: Api, a: dict):
    slug = _need(a, "tool")
    files = {_safe_path(k): str(v).encode() for k, v in (a.get("files") or {}).items()}
    binaries = {_safe_path(k): base64.b64decode(v) for k, v in (a.get("base64") or {}).items()}
    if not files and not binaries:
        raise ApiError("files is required: the whole folder, as a map of relative path -> file content")
    if not any(k in files or k in binaries for k in ("Dockerfile", "index.html")):
        raise ApiError("no Dockerfile and no index.html at the top level. A Boathouse tool is a folder with a Dockerfile "
                       "whose process listens on $PORT (8080), or a website folder with an index.html at the top "
                       "(Boathouse adds the nginx Dockerfile itself). Add one and deploy again.")
    form = {k: str(v) for k, v in (("note", a.get("note")), ("name", a.get("name")), ("base", a.get("base"))) if v is not None}
    blob = _pack(files, binaries)
    r = await api.post(f"/api/tools/{slug}/deploys", data=form,
                       files={"context": (f"{slug}.tar.gz", blob, "application/gzip")})
    j = r.json()
    t = (await api.get(f"/api/tools/{slug}")).json()
    others = max(0, len(t.get("grants") or []) - 1)      # the deployer holds a grant too; count the other people
    text = (f"live: {j['url']}  (release #{j['release']})\n"
            f"visible to: {_who(t)}" + (f", shared with {others} more" if others else "") +
            f"\npulled from release #{j['release']}: pass base {j['release']} on your next deploy of this tool")
    return text, j


async def t_pull(api: Api, a: dict):
    slug = _need(a, "tool")
    params = {"release": a["release"]} if a.get("release") else None
    r = await api.get(f"/api/tools/{slug}/source", params=params)
    seq = int(r.headers.get("X-Boathouse-Release") or a.get("release") or 0)
    out: dict[str, str] = {}
    binary: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
        for m in tf.getmembers():
            if m.name.startswith(("/", "..")) or "/../" in m.name or m.name.split("/")[0] == "..":
                raise ApiError(f"refusing suspicious path in the archive: {m.name}")
            if not m.isfile():
                continue
            data = tf.extractfile(m).read()
            try:
                out[m.name] = data.decode("utf-8")
            except UnicodeDecodeError:
                binary[m.name] = base64.b64encode(data).decode()
    payload: dict[str, Any] = {"release": seq, "files": out}
    if binary:
        payload["binary_base64"] = binary
    names = sorted(list(out) + list(binary))
    text = (f"pulled {slug} release #{seq}: {len(names)} file(s)\n  " + "\n  ".join(names) +
            f"\nEdit these and deploy them back with base {seq} so a newer release is not overwritten.")
    return text, payload


async def t_logs(api: Api, a: dict):
    slug = _need(a, "tool")
    params: dict[str, Any] = {"tail": int(a.get("tail") or 200)}
    if a.get("since"):
        params["since"] = int(a["since"])
    text = (await api.get(f"/api/tools/{slug}/logs", params=params)).text
    return (text or f"no log lines for {slug} (is it running?)"), None


async def t_releases(api: Api, a: dict):
    slug = _need(a, "tool")
    rows = (await api.get(f"/api/tools/{slug}/releases")).json()
    lines = []
    for r in rows:
        lines.append(f"#{r['seq']:<4}{r['status']:<12}{_ago(r['created']):<12}{r['created_by']:<34}  {r.get('note') or ''}")
        if r["status"] == "failed" and r.get("log"):
            lines.append("      " + _error_line(r["log"]))
    return ("\n".join(lines) or f"{slug} has no releases yet"), {"items": rows}


_error_line = deploy.cause_line


async def t_rollback(api: Api, a: dict):
    slug = _need(a, "tool")
    body = {"to": int(a["to"])} if a.get("to") else {}
    j = (await api.post(f"/api/tools/{slug}/rollback", json=body)).json()
    return f"rolled back: {j['url']} now on image {j['image']} (release #{j['release']})", j


async def t_restart(api: Api, a: dict):
    slug = _need(a, "tool")
    j = (await api.post(f"/api/tools/{slug}/restart", json={})).json()
    return f"restarted {slug}; state {j['status']['state']}", j


async def t_share(api: Api, a: dict):
    slug, email = _need(a, "tool"), _need(a, "email")
    labels = a.get("labels") or []
    j = (await api.post(f"/api/tools/{slug}/grants", json={"email": email, "tier": a.get("tier") or "viewer", "labels": labels})).json()
    what = {"viewer": "look", "editor": "change the data", "admin": "change anything, including the software"}[j["tier"]]
    text = (f"{j['email']} can open {j['url']} as {j['tier']} ({what})" +
            (f", labels: {j['labels']}" if j["labels"] else "")) + _invite_note(j)
    return text, j


async def t_unshare(api: Api, a: dict):
    slug, email = _need(a, "tool"), _need(a, "email")
    j = (await api.delete(f"/api/tools/{slug}/grants/{quote(email)}")).json()
    return f"{j['removed']} can no longer open {slug}", j


async def t_set_access(api: Api, a: dict):
    slug, mode = _need(a, "tool"), _need(a, "mode")
    body: dict[str, Any] = ({"public": True} if mode == "public" else {"default_access": mode, "public": False})
    if a.get("tier"):
        body["default_tier"] = a["tier"]
    j = (await api.patch(f"/api/tools/{slug}", json=body)).json()
    if j.get("public"):
        return f"{j['slug']}: public, anyone may read it; changing it still needs sign-in", j
    if j["default_access"] == "members":
        return f"{j['slug']}: anyone in the workspace can open it as {j['default_tier']}; people it was shared with keep their own tier", j
    return f"{j['slug']}: only the people it was shared with can open it", j


async def t_secrets_list(api: Api, a: dict):
    slug = _need(a, "tool")
    rows = (await api.get(f"/api/tools/{slug}/secrets")).json()
    lines = [f"{s['name']:<32}{_ago(s['updated']):<12}{s['updated_by']}" for s in rows]
    return ("\n".join(lines) or f"{slug} has no secrets set"), {"items": rows}


async def t_secrets_set(api: Api, a: dict):
    slug, name = _need(a, "tool"), _need(a, "name")
    j = (await api.put(f"/api/tools/{slug}/secrets/{name}", json={"value": a.get("value", "")})).json()
    return f"set {j['set']}" + (" and restarted the tool" if j["restarted"] else ""), j


async def t_secrets_delete(api: Api, a: dict):
    slug, name = _need(a, "tool"), _need(a, "name")
    j = (await api.delete(f"/api/tools/{slug}/secrets/{name}")).json()
    return f"deleted {j['deleted']}; {j['note']}", j


async def t_users_list(api: Api, a: dict):
    rows = (await api.get("/api/users")).json()
    lines = [f"{u['role']:<8}{u['email']:<40}{(u.get('name') or ''):<24}"
             f"{'' if u.get('has_password') else '(no password yet: users_invite)'}" for u in rows]
    return "\n".join(lines), {"items": rows}


async def t_users_add(api: Api, a: dict):
    email = _need(a, "email")
    j = (await api.post("/api/users", json={"email": email, "role": a.get("role") or "member"})).json()
    return f"{j['email']} is a {j['role']}" + _invite_note(j), j


async def t_users_invite(api: Api, a: dict):
    email = _need(a, "email")
    j = (await api.post(f"/api/users/{quote(email)}/invite", json={})).json()
    if j.get("sign_in_url"):
        return f"{j['email']} already has a Boat House account. Sign in at {j['sign_in_url']}; use Forgot password there if needed.", j
    return f"Invitation ready for {j['email']}." + _invite_note(j), j


async def t_users_remove(api: Api, a: dict):
    email = _need(a, "email")
    j = (await api.delete(f"/api/users/{quote(email)}")).json()
    return f"removed {j['removed']}; {j['note']}", j


async def t_domains_list(api: Api, a: dict):
    j = (await api.get("/api/domains")).json()
    lines = [f"free address   {j['free']}", f"primary        {j['primary']}"]
    for d in j["domains"]:
        flags = ("primary " if d["primary"] else "") + ("dns-ok" if d["dns_ok"] else "dns-pending")
        lines.append(f"{d['domain']:<32}{d['registrar']:<10}{d['status']:<8}{flags}")
    return "\n".join(lines), j


async def t_domain_check(api: Api, a: dict):
    d = _need(a, "domain")
    q = (await api.post("/api/domains/check", json={"domain": d})).json()
    if not q["available"]:
        return f"{q['domain']}: taken", q
    total, margin = q.get("total_cents", q["price_cents"]), q.get("margin_cents", 0)
    promo = f" (first year; renews at {_money(q.get('renewal_total_cents', q['regular_cents']))})" if q["first_year_promo"] else ""
    text = (f"{q['domain']}: available, {_money(total)}/year ({_money(q['price_cents'])} registrar + {_money(margin)} Boathouse){promo}" +
            ("  PREMIUM, not buyable by API" if q["premium"] else ""))
    return text, q


async def t_domain_buy(api: Api, a: dict):
    d = _need(a, "domain")
    if type(a.get("confirm", False)) is not bool:
        raise ApiError("confirm must be true or false")
    if a.get("confirm", False):
        if not a.get("quote_id") or type(a.get("max_cost_cents")) is not int:
            raise ApiError("First request a quote; confirm with its quote_id and your approved max_cost_cents.")
        j = (await api.post("/api/domains", json={"domain": d, "confirm": True,
             "quote_id": a["quote_id"], "max_cost_cents": a["max_cost_cents"]})).json()
        lines = [f"bought {j['bought']} for {_money(j['cost_cents'])} (order {j['order_id']}); balance now {_money(j['balance_cents'])}"]
        lines += [f"  dns {rec['name']:<34}A  {rec['content']}" for rec in j["dns"]]
        lines.append(f"home {j['urls']['home']}   sign-in {j['urls']['auth']}   tools {j['urls']['tools']}")
        lines.append(j["note"])
        return "\n".join(lines), j
    q = (await api.post("/api/domains", json={"domain": d, "confirm": False})).json()
    sb = " [SANDBOX: fake money]" if q.get("sandbox") else ""
    quoted = (f"{q['domain']}: {q['cost']} now (registrar {_money(q['registrar_cents'])} + Boathouse "
              f"{_money(q['margin_cents'])}), renews at {_money(q['renewal_cents'])}/year; workspace balance "
              f"{_money(q['balance_cents'])}{sb}")
    if not q.get("would_succeed", True):
        raise ApiError(quoted + "\n" + (q.get("message") or "the registrar says this would fail"), q)
    return quoted + "\n" + q.get("renewal_policy", "") + "\nDry run only: nothing was bought. Confirm this quote_id with confirm true and max_cost_cents within the user's approved budget. Reuse the same quote on retries.", q


async def t_billing(api: Api, a: dict):
    b = (await api.get("/api/billing")).json()
    state = "PAUSED" if b["paused"] else "active"
    lines = [f"workspace {b['workspace']}: balance {_money(b['balance_cents'])} ({state}); "
             f"{b['running_tools']} running tool(s) burn {_money(b['burn_cents_per_day'])}/day" +
             (f", about {b['days_left']} days left" if b["days_left"] is not None else ""),
             "card on file: " + ("yes" if b["card_on_file"] else "no (use card_link)")]
    if b.get("plan"):
        lines.append(f"Organization plan: {_money(b['organization_month_cents'])}/month for up to {b['included_tools']} lightweight tools.")
        usage = b.get("organization_usage") or {}
        if usage.get("enforced"):
            lines.append(f"Shared app memory: {usage['memory_bytes']/1024**2:.1f} of {usage['memory_limit_bytes']/1024**2:g} MB; compute cap: {usage['cpu_limit_cores']:g} CPU core.")
        if usage.get("message"): lines.append(usage["message"])
    for l in b["ledger"][:10]:
        lines.append(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(l['ts'])):<18}{l['kind']:<8}"
                     f"{_money(l['amount_cents']):>10}{_money(l['balance_after']):>10}  {l['memo'] or ''}")
    return "\n".join(lines), b


async def t_prices(api: Api, a: dict):
    p = (await api.get("/api/prices")).json()
    return "\n".join("· " + w for w in p["words"]), p


async def t_referral(api: Api, a: dict):
    r = (await api.get("/api/referral")).json()
    lines = [f"your code: {r['code']}    link: {r['link']}", f"the deal: {r['terms']}"]
    if r.get("referred"):
        for x in r["referred"]:
            lines.append(f"  {x['name']:<28} since {time.strftime('%Y-%m-%d', time.gmtime(x['since']))}   "
                         f"earned you {_money(x['earned_cents'])}   unpaid {_money(x['unpaid_cents'])}")
        lines.append(f"total earned {_money(r['earned_cents'])}, not yet paid out {_money(r['unpaid_cents'])} (paid monthly, in cash)")
    else:
        lines.append("nobody has signed up with it yet.")
    return "\n".join(lines), r


async def t_request_access(api: Api, a: dict):
    target = _need(a, "target")
    if "/" not in target:
        raise ApiError("target is <workspace>/<tool>, for example shared/db")
    ws, tool = target.split("/", 1)
    tier = a.get("tier") or "viewer"
    r = (await api.post("/api/access-requests", json={"workspace": ws, "tool": tool, "tier": tier, "message": a.get("message") or ""})).json()
    if r.get("already"):
        return f"you already have {r['tier']} on {ws}/{tool}; nothing to ask for. Work on it: pull {ws}/{tool}", r
    n = r.get("asked_count") or 0
    who = "its owner" if n == 1 else f"its {n} owners and admins" if n else "its owners"
    if r.get("emailed"):
        text = f"asked for {tier} on {ws}/{tool}. {who} got an email with one Allow button (good for {r['expires_days']} days)."
    else:
        text = f"asked for {tier} on {ws}/{tool}; email is not set up on this Boathouse, so tell {who} to allow it (access_requests, then allow_request)."
    return text + " The person gets an email the moment it is allowed.", r


async def t_access_requests(api: Api, a: dict):
    rows = (await api.get("/api/access-requests")).json()["requests"]
    if not rows:
        return "nobody is waiting to be let in.", {"items": []}
    lines = [f"{q['id']}   {q['email']} asks for {q['tier']} on {q['tool']}" + (f'   "{q["message"]}"' if q.get("message") else "")
             for q in rows]
    return "\n".join(lines) + "\nallow one: allow_request with its id", {"items": rows}


async def t_allow_request(api: Api, a: dict):
    rid = _need(a, "id")
    r = (await api.post(f"/api/access-requests/{quote(str(rid))}/allow", json={})).json()
    return (f"{r['email']} is now {r['tier']} on {r['tool']}" +
            (", and got an email saying what to do next." if r.get("emailed") else ".")), r


async def t_domain_renewals(api: Api, a: dict):
    result = (await api.get("/api/domains/renewals")).json()
    return "Domain renewal settings, including detached registrations. Changes require owner approval.", result


async def t_domain_renewal_set(api: Api, a: dict):
    if a.get("confirm") is not True:
        return "Preview only. Enabling renewal authorizes future prepaid charges up to max_cost_cents per renewal, including the service fee. Turning it off lets the domain expire. Get approval, then repeat with confirm true.", a
    d = a.get("domain", "").strip().lower()
    result = (await api.patch(f"/api/domains/{quote(d)}/renewal", json={"enabled": a.get("enabled"), "max_cost_cents": a.get("max_cost_cents")})).json()
    return "Renewal settings saved. The current registration is unchanged.", result


async def t_domain_attach(api: Api, a: dict):
    d = _need(a, "domain")
    r = (await api.post(f"/api/domains/{quote(d)}/attach", json={})).json()
    lines = [f"attached {r['attached']} ({r['registrar']}" + (", now primary)" if r.get("primary") else ")")]
    if a.get("tool"):
        await api.patch(f"/api/domains/{quote(d)}", json={"tool": a["tool"]})
        lines.append(f"{d} and www.{d} will open the tool {a['tool']} once the DNS below points here")
    for rec in r.get("dns", []):
        lines.append(f"  dns {rec['name']:<34}A  {rec['content']}")
    for rec in r.get("make_these_records", []):
        lines.append(f"  make this record at your DNS host: {rec['name']:<4}{rec['type']}  {rec['content']}")
    if r.get("note"):
        lines.append(r["note"])
    return "\n".join(lines), r


async def t_domain_point(api: Api, a: dict):
    d = _need(a, "domain")
    tool = a.get("tool") or ""
    r = (await api.patch(f"/api/domains/{quote(d)}", json={"tool": tool})).json()
    return (f"{d} and www.{d} now open the tool {tool}" if tool else f"{d} shows the tool list again"), r


async def t_domain_primary(api: Api, a: dict):
    d = _need(a, "domain")
    r = (await api.patch(f"/api/domains/{quote(d)}", json={"primary": True})).json()
    return f"primary is now {r['primary']}", r


async def t_domain_dns(api: Api, a: dict):
    d = _need(a, "domain")
    r = (await api.get(f"/api/domains/{quote(d)}/dns")).json()
    lines = [f"{rec.get('type', ''):<7}{rec.get('name', ''):<40}{rec.get('content', '')}" for rec in r.get("records", [])]
    return "\n".join(lines) or "no records", r


async def t_domain_repoint(api: Api, a: dict):
    d = _need(a, "domain")
    r = (await api.post(f"/api/domains/{quote(d)}/dns", json={})).json()
    return "\n".join(f"dns {rec['name']:<34}A  {rec['content']}" for rec in r.get("records", [])) or "no records changed", r


async def t_domain_detach(api: Api, a: dict):
    d = _need(a, "domain")
    r = (await api.delete(f"/api/domains/{quote(d)}")).json()
    return f"detached {r['detached']}; {r['note']}", r


async def t_topup(api: Api, a: dict):
    cents = _need(a, "cents")
    confirm = a.get("confirm", False)
    if type(confirm) is not bool:
        raise ValueError("confirm must be true or false")
    if confirm:
        operation_id = _need(a, "operation_id")
        j = (await api.post("/api/billing/topup", json={"cents": cents, "confirm": True, "operation_id": operation_id})).json()
        if j.get("requires_checkout"):
            return j["message"] + "\n" + (j.get("checkout_url") or "Open your account to check this payment."), j
        return (f"charged {_money(j['cents'])}; balance now {_money(j['balance_cents'])}" +
                (" (workspace resumed)" if j.get("resumed") else "")), j
    q = (await api.post("/api/billing/topup", json={"cents": cents, "operation_id": a.get("operation_id")})).json()
    quoted = q["message"] + f" Balance after: {_money(q['balance_after_cents'])}."
    return quoted + f"\nDry run only: nothing was charged. After the user's approval, call again with confirm true and operation_id {q['operation_id']}. Reuse this ID for any retry.", q


async def t_usage(api,a):
    result=(await api.get(f"/api/tools/{_need(a,'tool')}/usage")).json()
    return result['message'],result

async def t_capacity(api,a):
    result=(await api.post(f"/api/tools/{_need(a,'tool')}/capacity",json={k:v for k,v in a.items() if k in ('storage_gb','quote_id','confirm','max_extra_monthly_cents')})).json()
    return result['message'],result


async def t_card_link(api: Api, a: dict):
    j = (await api.post("/api/billing/card", json={})).json()
    return ("Open this once and enter the card. That is the only billing step that needs a browser.\n"
            f"  {j['url']}"), j


async def t_delete_tool(api: Api, a: dict):
    slug = _need(a, "tool")
    purge = bool(a.get("purge"))
    if purge and not a.get("purge_confirm"):
        raise ApiError(f"purge deletes {slug}'s database and files forever. Call again with purge true AND "
                       "purge_confirm true, or leave purge out to keep the data.")
    j = (await api.delete(f"/api/tools/{slug}", params={"purge": "true" if purge else "false"})).json()
    return (f"deleted {j['deleted']}" + (" and purged its database and files" if j["data_purged"] else
                                         f"; its database, files, sharing and secrets are kept and all come back when a tool named "
                                         f"{j['deleted']} is deployed again")), j


# ---- the catalogue -----------------------------------------------------------

TOOL_ARG = {"type": "string", "description": "The tool's short name, for example 'finance', or 'acme/finance' to select its workspace."}
EMAIL_ARG = {"type": "string", "description": "The person's email address."}
CONFIRM_ARG = {"type": "boolean", "description": "False or absent returns the quote and does nothing. True actually does it."}


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": {"workspace": {"type": "string", "description": "Workspace slug to act in. Use this when you belong to several workspaces; whoami lists them."}, **props}, "required": required or []}


Handler = Callable[[Api, dict], Awaitable[tuple[str, Any]]]
TOOLS: list[tuple[str, str, dict, Handler]] = [
    ("whoami",
     "Who this project key belongs to: the email, the workspace, the domain its tools live on, and the "
     "prepaid balance. Call it first if you are not sure which workspace you are acting in.",
     _schema({}), t_whoami),

    ("list_tools",
     "Every tool in the workspace with its state (running/absent), current release, public URL and who can "
     "see it. A 'tool' is one small web app Boathouse runs behind the workspace login.",
     _schema({}), t_list_tools),

    ("get_tool",
     "One tool in detail: URL, container state, current release, who it is visible to, and the list of "
     "people it has been shared with.",
     _schema({"tool": TOOL_ARG}, ["tool"]), t_get_tool),

    ("deploy",
     "Deploy a folder as a tool and make it live at https://<tool>.<workspace domain>. Send the whole folder "
     "in `files`, which MUST include a top-level Dockerfile whose process listens on $PORT (8080). Creates the "
     "tool on the first deploy; afterwards it replaces the running container. If you got these files from "
     "`pull`, pass the release number you pulled as `base` so a deploy someone else made in the meantime is "
     "not silently overwritten.",
     _schema({
         "tool": TOOL_ARG,
         "files": {"type": "object", "description": "The folder: relative path -> the file's text content, e.g. "
                                                    "{\"Dockerfile\": \"FROM python:3.12-slim\\n...\", \"app.py\": \"...\"}. "
                                                    "No leading slash and no '..' in paths.",
                   "additionalProperties": {"type": "string"}},
         "base64": {"type": "object", "description": "Optional binary files: relative path -> base64 of the bytes.",
                    "additionalProperties": {"type": "string"}},
         "name": {"type": "string", "description": "Human name for the tool, used when it is created (e.g. 'Starter Finance')."},
         "note": {"type": "string", "description": "Why this release exists; shown in the release list."},
         "base": {"type": "integer", "description": "The release number these files were pulled from. The deploy is "
                                                    "refused with STALE_BASE if a newer release is live."},
     }, ["tool", "files"]), t_deploy),

    ("pull",
     "Fetch the exact source folder a release was built from, so you can edit it and deploy it back. Returns "
     "the release number and the files as text (binary files come back base64 encoded). Tool admins only.",
     _schema({"tool": TOOL_ARG,
              "release": {"type": "integer", "description": "Which release to fetch. Defaults to the live one."}},
             ["tool"]), t_pull),

    ("logs",
     "The tool container's recent log lines, newest last. Use this to find out why a tool is failing.",
     _schema({"tool": TOOL_ARG,
              "tail": {"type": "integer", "description": "How many lines from the end (default 200, max 5000)."},
              "since": {"type": "integer", "description": "Unix seconds: only lines after this moment."}},
             ["tool"]), t_logs),

    ("releases",
     "The last 20 releases of a tool: number, status (live/superseded/failed), when, who and the note. A failed "
     "release shows the last line of its build log.",
     _schema({"tool": TOOL_ARG}, ["tool"]), t_releases),

    ("rollback",
     "Put an earlier release back in service. With no `to`, goes back to the release before the live one. This "
     "rolls back the software, never the database.",
     _schema({"tool": TOOL_ARG,
              "to": {"type": "integer", "description": "Release number to restore. Defaults to the previous one."}},
             ["tool"]), t_rollback),

    ("restart",
     "Restart the tool's container with its current release and current secrets.",
     _schema({"tool": TOOL_ARG}, ["tool"]), t_restart),

    ("share",
     "Give one person access to one tool by email, at a tier: viewer (read only; the gateway blocks their "
     "writes), editor (may change the data), admin (may change the software, secrets, sharing). If the person "
     "is new, Boat House emails a secure invitation when email is configured. Check emailed in the result; if delivery failed, share the invitation page so the person can request their secure email.",
     _schema({"tool": TOOL_ARG, "email": EMAIL_ARG,
              "tier": {"type": "string", "enum": ["viewer", "editor", "admin"],
                       "description": "viewer, editor or admin. Default viewer."},
              "labels": {"type": "array", "items": {"type": "string"},
                         "description": "Optional app-specific words passed to the tool as headers (e.g. ['consultant']). "
                                        "Labels are not permissions."}},
             ["tool", "email"]), t_share),

    ("unshare",
     "Take away one person's access to one tool. They keep their workspace account.",
     _schema({"tool": TOOL_ARG, "email": EMAIL_ARG}, ["tool", "email"]), t_unshare),

    ("request_access",
     "Ask to be let into a tool in someone else's workspace, at viewer, editor or admin. Its owners get an "
     "email with one Allow button; nobody there runs a command. The person is emailed when it is allowed.",
     _schema({"target": {"type": "string", "description": "<workspace>/<tool>, for example shared/db."},
              "tier": {"type": "string", "enum": ["viewer", "editor", "admin"], "description": "Default viewer."},
              "message": {"type": "string", "description": "One line the owners see, e.g. who you are and why."}},
             ["target"]), t_request_access),

    ("access_requests",
     "Everyone waiting to be let into one of this workspace's tools: request id, email, tier asked for, "
     "and their message. Owners and tool admins.",
     _schema({}), t_access_requests),

    ("allow_request",
     "Let a waiting person in at the tier they asked for. Their tool grant is made and they get an email "
     "saying what to do next. Owners and tool admins.",
     _schema({"id": {"type": "string", "description": "The request id from access_requests."}}, ["id"]), t_allow_request),

    ("set_access",
     "Set who a tool is open to by default: 'members' means anyone in the workspace (at the given tier), "
     "'listed' means only the people it has been explicitly shared with. Use 'listed' for anything sensitive.",
     _schema({"tool": TOOL_ARG,
              "mode": {"type": "string", "enum": ["members", "listed", "public"], "description": "members (anyone in the workspace), listed (only people shared with), or public (anyone on the internet may read; writes still need sign-in)."},
              "tier": {"type": "string", "enum": ["viewer", "editor", "admin"],
                       "description": "The tier workspace members get when mode is 'members'."}},
             ["tool", "mode"]), t_set_access),

    ("secrets_list",
     "The names of a tool's secrets (API keys and the like), when each was last set and by whom. Values are "
     "never returned.",
     _schema({"tool": TOOL_ARG}, ["tool"]), t_secrets_list),

    ("secrets_set",
     "Set one secret on a tool. It is injected as an environment variable of that name and the tool is "
     "restarted. Names are UPPER_SNAKE_CASE. Never echo the value back to the user.",
     _schema({"tool": TOOL_ARG,
              "name": {"type": "string", "description": "UPPER_SNAKE_CASE environment variable name, e.g. STRIPE_KEY."},
              "value": {"type": "string", "description": "The secret value."}},
             ["tool", "name", "value"]), t_secrets_set),

    ("secrets_delete",
     "Remove one secret from a tool. It disappears on the tool's next restart or deploy.",
     _schema({"tool": TOOL_ARG, "name": {"type": "string", "description": "The secret's name."}},
             ["tool", "name"]), t_secrets_delete),

    ("users_list",
     "Everyone in the workspace: role (owner, member or guest), name, and whether they have set a password yet.",
     _schema({}), t_users_list),

    ("users_add",
     "Add a person to the workspace, or change their role. owner = may spend money, buy domains and manage "
     "people, and is admin on every tool; member = may see tools open to members and deploy their own; guest = "
     "sees only what is explicitly shared with them. Returns a one-time invite link for someone new. Owners only.",
     _schema({"email": EMAIL_ARG,
              "role": {"type": "string", "enum": ["member", "owner", "guest"], "description": "Default member."}},
             ["email"]), t_users_add),

    ("users_invite",
     "Resend an invitation to someone in the workspace. New accounts confirm their mailbox through the emailed "
     "link; established accounts receive a normal sign-in URL. Password recovery is self-service on that page, "
     "and only the account holder's mailbox receives a reset link. Check emailed before offering a fallback.",
     _schema({"email": EMAIL_ARG}, ["email"]), t_users_invite),

    ("users_remove",
     "Remove a person from the workspace. Their sessions, project keys, invites and tool grants stop working "
     "immediately. Owners only.",
     _schema({"email": EMAIL_ARG}, ["email"]), t_users_remove),

    ("domains_list",
     "The addresses this workspace answers on: the free <workspace>.boathousecloud.com address, the primary "
     "domain, and every custom domain with its DNS state.",
     _schema({}), t_domains_list),

    ("domain_check",
     "Ask the registrar whether a domain is free and what it costs per year. Buys nothing. total_cents is the price "
     "the workspace pays (registrar_cents plus Boathouse's margin_cents); show that one.",
     _schema({"domain": {"type": "string", "description": "A full domain name, e.g. 'tools.example.com'."}},
             ["domain"]), t_domain_check),

    ("domain_buy",
     "Buy a domain through Boathouse's registrar and point it at this box, paid from the workspace's prepaid "
     "balance. Without confirm it only returns the quote (cost now, renewal price, your balance) and buys "
     "nothing. Owners only.",
     _schema({"domain": {"type": "string", "description": "The domain to buy, e.g. 'tools.example.com'."},
              "confirm": CONFIRM_ARG,
              "quote_id": {"type": "string", "description": "The quote_id from the dry run. Required to confirm; reuse it on retries."},
              "max_cost_cents": {"type": "integer", "minimum": 1, "description": "Maximum approved total, including Boathouse's margin. Required to confirm."}}, ["domain"]), t_domain_buy),

    ("domain_renewals", "Read domain expiry dates, renewal status, spending limits and billing problems. Owners only.", _schema({}), t_domain_renewals),
    ("domain_renewal_set", "Set automatic renewal and a maximum prepaid renewal charge. Quote the change first, then require explicit approval with confirm true. Turning renewal off lets the domain expire. Owners only.",
     _schema({"domain":{"type":"string","description":"A domain owned through this organization, including detached domains."},"enabled":{"type":"boolean","description":"True renews from prepaid credit; false lets the domain expire."},"max_cost_cents":{"type":"integer","minimum":1,"maximum":100000,"description":"Approved maximum per renewal including the service fee; required when enabling."},"confirm":CONFIRM_ARG},["domain","enabled"]),t_domain_renewal_set),

    ("domain_attach",
     "Bring a domain the workspace already owns elsewhere: Boathouse starts answering on it and returns the DNS "
     "records to make at the current DNS host. Optionally point it at one tool at the same time. Owners only.",
     _schema({"domain": {"type": "string", "description": "A full domain name you control, e.g. 'tools.example.com'."},
              "tool": TOOL_ARG | {"description": "Optional: the tool the bare domain and www should open."}},
             ["domain"]), t_domain_attach),

    ("domain_point",
     "Make a domain (and its www) open one tool instead of the workspace's tool list. An empty tool clears it. "
     "Owners only.",
     _schema({"domain": {"type": "string", "description": "A domain the workspace answers on."},
              "tool": TOOL_ARG | {"description": "The tool to open, or an empty string to show the tool list again."}},
             ["domain"]), t_domain_point),

    ("domain_primary",
     "Make one of the workspace's domains the primary one: the address tool URLs and emails use. Owners only.",
     _schema({"domain": {"type": "string", "description": "A domain the workspace answers on."}}, ["domain"]), t_domain_primary),

    ("domain_dns",
     "The DNS records the registrar currently holds for a domain Boathouse bought or manages. Changes nothing.",
     _schema({"domain": {"type": "string", "description": "A domain the workspace answers on."}}, ["domain"]), t_domain_dns),

    ("domain_repoint",
     "Rewrite a Boathouse-managed domain's DNS records to point at this Boathouse again (after a move, or if "
     "someone changed them). Owners only.",
     _schema({"domain": {"type": "string", "description": "A domain the workspace answers on."}}, ["domain"]), t_domain_repoint),

    ("domain_detach",
     "Stop serving a domain. The registration itself is untouched and domain_attach brings it back. Owners only.",
     _schema({"domain": {"type": "string", "description": "A domain the workspace answers on."}}, ["domain"]), t_domain_detach),

    ('usage','Check storage, memory, CPU and the reason an app is paused. Tool admins only.',_schema({'tool':TOOL_ARG},['tool']),t_usage),
    ('capacity','Quote additional app storage. Show the maximum extra monthly price and get owner approval before confirming. No automatic upgrade. Owners only.',
     _schema({'tool':TOOL_ARG,'storage_gb':{'type':'integer','description':'Requested total storage in whole GB; starts with a price quote.'},'confirm':CONFIRM_ARG,'quote_id':{'type':'string','description':'The exact capacity quote the owner approved.'},'max_extra_monthly_cents':{'type':'integer','description':'Maximum extra monthly price from the approved quote, in cents.'}},['tool']),t_capacity),

    ("billing",
     "The workspace's money: prepaid balance, whether it is paused, how fast running tools burn credit, how "
     "many days are left, whether a card is on file, and the last ledger lines.",
     _schema({}), t_billing),

    ("prices",
     "What Boathouse charges, in plain words: one organization plan for up to five lightweight tools, shared limits, storage, and the domain margin.",
     _schema({}), t_prices),

    ("referral",
     "This person's referral code and link, the deal in words, and what each referred workspace has earned "
     "them so far (10% of usage charges, paid monthly in cash).",
     _schema({}), t_referral),

    ("topup",
     "Open secure Stripe checkout to add prepaid hosting credit; the owner completes payment there. Without confirm it only "
     "quotes and returns operation_id. Confirm the same cents and operation_id after user approval; reuse the ID on retries. Between $5.00 (500) and $1,000.00 (100000). Owners only.",
     _schema({"cents": {"type": "integer", "description": "Amount in cents, e.g. 2000 for $20.00."},
              "confirm": CONFIRM_ARG,
              "operation_id": {"type": "string", "description": "The payment quote ID, required when confirming or retrying."}}, ["cents"]), t_topup),

    ("card_link",
     "A one-time link where a workspace owner types a card into Stripe. This is the only part of Boathouse "
     "that needs a browser. Owners only.",
     _schema({}), t_card_link),

    ("delete_tool",
     "Delete a tool: its container, its releases and its sharing. By default the tool's database and files are "
     "kept. purge true also destroys them forever, and then requires purge_confirm true as well.",
     _schema({"tool": TOOL_ARG,
              "purge": {"type": "boolean", "description": "Also destroy the tool's database and uploaded files. Irreversible."},
              "purge_confirm": {"type": "boolean", "description": "Must be true alongside purge, as a second confirmation."}},
             ["tool"]), t_delete_tool),
]

TOOL_LIST = [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOLS]
HANDLERS: dict[str, Handler] = {n: h for n, _, _, h in TOOLS}


# =============================================================================
# JSON-RPC
# =============================================================================

def _result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


_BH_TO_MCP = [  # longest first; the API speaks bh, an MCP client hears tool names
    ("run bh pull", "call pull"), ("bh pull", "pull"), ("bh billing card", "card_link"), ("bh billing topup", "topup"),
    ("bh billing", "billing"), ("bh users invite", "users_invite"), ("bh requests", "access_requests"), ("bh allow", "allow_request"),
    ("bh unshare", "unshare"), ("bh share", "share"), ("bh releases", "releases"), ("bh rollback", "rollback"),
    ("bh restart", "restart"), ("bh deploy", "deploy"), ("bh logs", "logs"), ("bh rm", "delete_tool"), ("bh restore", "restore (bh only)"),
]


def _speak_mcp(text: str) -> str:
    for bh, tool in _BH_TO_MCP:
        text = text.replace(bh, tool)
    return text


def _tool_result(text: str, structured: Any = None, is_error: bool = False) -> dict:
    out: dict[str, Any] = {"content": [{"type": "text", "text": _speak_mcp(text)}], "isError": is_error}
    if isinstance(structured, dict):   # the sentences inside the structured payload speak MCP too
        out["structuredContent"] = {k: (_speak_mcp(v) if isinstance(v, str) else v) for k, v in structured.items()}
    return out


async def _call_tool(params: dict, authorization: str | None, workspace: str | None = None) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if name not in HANDLERS:
        return _tool_result(f"there is no Boathouse tool called {name!r}. Call tools/list to see them.", None, True)
    if not authorization:
        return _tool_result(
            "No Boathouse key on this connection. Every Boathouse tool acts as a workspace, so this MCP "
            f"server needs the header `Authorization: Bearer <project key>`. Mint one on the workspace's account page "
            f"(https://{config.PLATFORM_DOMAIN}/account, under Keys) or with `bh keys create`. Set it on the MCP "
            "connection and try again.",
            None, True)
    if not isinstance(args, dict):
        return _tool_result("arguments must be an object", None, True)
    args = dict(args)
    selected = args.pop("workspace", None) or workspace
    if selected is not None and (not isinstance(selected, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", selected)):
        return _tool_result("workspace must be its short name, for example 'acme'.", None, True)
    if isinstance(args.get("tool"), str) and "/" in args["tool"]:
        tool_workspace, args["tool"] = args["tool"].split("/", 1)
        if selected and selected != tool_workspace:
            return _tool_result("The workspace and tool name select different workspaces. Use one matching workspace.", None, True)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", tool_workspace):
            return _tool_result("Invalid workspace in tool name.", None, True)
        selected = tool_workspace
    try:
        async with client_factory() as client:
            text, structured = await HANDLERS[name](Api(client, authorization, selected), args)
        return _tool_result(text, structured)
    except ApiError as e:
        return _tool_result(e.text, e.detail if isinstance(e.detail, dict) else None, True)
    except (httpx.HTTPError, OSError) as e:
        return _tool_result(f"could not reach the Boathouse API: {e}", None, True)
    except Exception as e:  # noqa: BLE001  an agent gets a message, never a stack trace
        return _tool_result(f"{name} failed: {type(e).__name__}: {e}", None, True)


async def _handle(msg: Any, authorization: str | None, workspace: str | None = None) -> dict | None:
    """One JSON-RPC message in, one response out (or None for a notification)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST,
                      "not a JSON-RPC 2.0 message: needs jsonrpc '2.0' and a method")
    method, msg_id = msg["method"], msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, INVALID_PARAMS, "params must be an object")
    if msg_id is None:  # a notification: do the work, say nothing
        return None
    if method == "initialize":
        return _result(msg_id, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                                "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOL_LIST})
    if method == "tools/call":
        return _result(msg_id, await _call_tool(params, authorization, workspace))
    return _error(msg_id, METHOD_NOT_FOUND, f"unknown method {method!r}; this server speaks initialize, ping, tools/list and tools/call")


def _host(request: Request) -> str:
    return (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()


def _is_mcp_host(request: Request) -> bool:
    return hosts.resolve(_host(request)).kind == "mcp"


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    if not _is_mcp_host(request):
        return JSONResponse({"error": "not found"}, 404)
    authorization = request.headers.get("authorization")
    workspace = request.headers.get("boathouse-workspace")
    try:
        body = json.loads(await request.body())
    except ValueError:
        return JSONResponse(_error(None, PARSE_ERROR, "the request body is not JSON"), 400, headers=JSONRPC_HEADERS)
    if isinstance(body, list):
        if not body:
            return JSONResponse(_error(None, INVALID_REQUEST, "an empty batch is not a request"), 400, headers=JSONRPC_HEADERS)
        replies = [r for r in [await _handle(m, authorization, workspace) for m in body] if r is not None]
        if not replies:
            return Response(status_code=202, headers=JSONRPC_HEADERS)
        return JSONResponse(replies, headers=JSONRPC_HEADERS)
    if not isinstance(body, dict):
        return JSONResponse(_error(None, INVALID_REQUEST, "expected a JSON-RPC object or a batch array"), 400, headers=JSONRPC_HEADERS)
    reply = await _handle(body, authorization, workspace)
    if reply is None:
        return Response(status_code=202, headers=JSONRPC_HEADERS)
    return JSONResponse(reply, headers=JSONRPC_HEADERS)


@router.get("/mcp")
async def mcp_get(request: Request):
    if not _is_mcp_host(request):
        return JSONResponse({"error": "not found"}, 404)
    return JSONResponse(
        {"error": "method not allowed", "detail": "Boathouse speaks Streamable HTTP MCP over POST only; there is no "
                                                  "SSE stream. POST JSON-RPC to this URL with header "
                                                  "'Authorization: Bearer <project key>'."},
        405, headers={**JSONRPC_HEADERS, "Allow": "POST"})
