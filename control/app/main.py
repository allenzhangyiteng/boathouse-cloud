"""Boathouse control plane.

Faces, chosen by hostname (see hosts.py):
  auth.<base>        sign-in, invites, sign-out, /api for the bh command
  <base>             the list of tools you can open
  <tool>.<base>      a tool; Caddy asks /internal/authz before every request
  api.<platform>     the API the bh command talks to
  /internal/*        Caddy only (forward-auth, on-demand TLS ask, down page)
"""
import asyncio
import io
import re
import json
import secrets
import shutil
import time
from urllib.parse import quote, urlparse, urlencode

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.background import BackgroundTask

from . import checkout, resources, auth, billing, config, db, deploy, domain_payments, export, hosts, mail, pages, referrals, registrar, restore
from .hosts import RESERVED

app = FastAPI(title="Boathouse", docs_url=None, redoc_url=None)
@app.exception_handler(referrals.ReferralError)
async def referral_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({'detail':str(exc)}, status_code=422)
@app.exception_handler(resources.ResourceError)
async def resource_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({'detail':str(exc)},status_code=409)

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
TIERS = db.TIERS
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
_deploy_locks: dict[str, asyncio.Lock] = {}
MAX_CONTEXT_BYTES = 200 * 1024 * 1024


def _same_origin(request: Request, origin: str | None) -> bool:
    if not origin:
        return False
    try:
        p = urlparse(origin)
        return (p.scheme == "https" and p.netloc.lower() == _host(request).lower()
                and not p.username and not p.password and not p.path and not p.query and not p.fragment)
    except ValueError:
        return False


@app.middleware("http")
async def browser_origin(request: Request, call_next):
    # Sibling customer tools are same-site for cookies, but are not trusted to
    # submit sign-in/account forms. Bearer clients do not rely on browser cookies.
    origin = request.headers.get("origin")
    if request.method not in SAFE_METHODS and origin and not request.headers.get("authorization") and not _same_origin(request, origin):
        return PlainTextResponse("Open this page on Boat House and try again.", 403)
    response = await call_next(request)
    # These responses come from the control plane, not customer app content.
    # Prevent another site from framing account/consent pages or MIME-sniffing
    # downloads. Customer apps keep their own embedding policy.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith(("/join/", "/verify-email/", "/reset-password/", "/handoff", "/api/claim")):
        response.headers["Cache-Control"] = "no-store"
        # no-referrer makes browser HTML form POSTs send Origin:null. Keep the
        # real HTTPS origin while excluding secret paths/queries from referrers.
        response.headers["Referrer-Policy"] = "strict-origin"
    return response


@app.on_event("startup")
def _startup():
    db.init()
    auth.master_key()
    config.BUILD_DIR.mkdir(parents=True, exist_ok=True)
    config.SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    # Caddy and Postgres must sit on every tool's private network. A recreated
    # gateway container forgets those attachments; put them back on every boot.
    with db.conn() as c:
        tools = c.execute("SELECT * FROM tools").fetchall()
    for tool in tools:
        try:
            deploy.ensure_network(tool)
        except Exception as e:  # noqa: BLE001
            print(f"startup: could not attach networks for {tool['slug']}: {e}")
    if config.RESOURCE_GUARD:
        asyncio.get_event_loop().create_task(_resource_loop())
    if config.METER:
        asyncio.get_event_loop().create_task(_meter_loop())


async def _resource_loop():
    while True:
        try: await run_in_threadpool(resources.monitor_once)
        except Exception as e: print(f'resource monitor: {type(e).__name__}')
        await asyncio.sleep(config.RESOURCE_INTERVAL)


async def _meter_loop():
    """Once an hour, charge today's running tools (idempotent per tool per day)."""
    await asyncio.sleep(60)
    while True:
        try:
            await run_in_threadpool(checkout.reconcile_pending)
            lines = await run_in_threadpool(billing.meter_once)
            if lines:
                db.audit("meter", "billing.metered", None, {"lines": len(lines), "cents": sum(x["cents"] for x in lines)})
        except Exception as e:  # noqa: BLE001
            print(f"meter: {e}")
        await asyncio.sleep(3600)


# =============================================================================
# helpers
# =============================================================================

def _host(request: Request) -> str:
    return (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()


def _where(request: Request) -> hosts.Where:
    return hosts.resolve(_host(request))


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()


def _owners(wid: str) -> list[str]:
    with db.conn() as c:
        return [r["email"] for r in c.execute("SELECT email FROM users WHERE workspace_id=? AND role='owner' ORDER BY created", (wid,))]


def _tool_by_slug(wid: str, slug: str):
    with db.conn() as c:
        return c.execute("SELECT * FROM tools WHERE workspace_id=? AND slug=?", (wid, slug)).fetchone()


def _user(wid: str, email: str):
    with db.conn() as c:
        return c.execute("SELECT * FROM users WHERE workspace_id=? AND email=?", (wid, email.lower())).fetchone()


def _row(r) -> dict | None:
    return dict(r) if r is not None else None


def _safe_next(url: str | None, base: str) -> str:
    """Only ever bounce back to a host under this workspace's own domain."""
    home = f"https://{base}/"
    if not url or "\\" in url or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return home
    try:
        p = urlparse(url)
        valid = p.scheme == "https" and p.port in (None, 443) and not p.username and not p.password and (p.hostname == base or (p.hostname or "").endswith("." + base))
    except ValueError:
        valid = False
    if not valid:
        return home
    return url


def _set_session(resp, where: hosts.Where, sid: str):
    resp.set_cookie(config.SESSION_COOKIE, sid, max_age=config.SESSION_DAYS * 86400, domain=where.cookie_domain,
                    secure=True, httponly=True, samesite="lax", path="/")


def _tier_for(user, tool) -> tuple[str, str] | None:
    """(tier, labels) this person has on this tool, or None for no access.
    Workspace owners are admin everywhere. Otherwise an explicit grant wins;
    members get the tool's default tier when it is open to members; guests
    need a grant."""
    if user is None:
        return None
    with db.conn() as c:
        g = c.execute("SELECT tier, labels FROM grants WHERE tool_id=? AND email=?", (tool["id"], user["email"])).fetchone()
    if user["role"] == "owner":
        return "admin", (g["labels"] if g else "")
    if g:
        return g["tier"], g["labels"] or ""
    if user["role"] == "member" and tool["default_access"] == "members":
        return tool["default_tier"], ""
    return None


def _is_tool_admin(user, tool) -> bool:
    t = _tier_for(user, tool)
    return bool(t and t[0] == "admin")


def _require_tool_admin(user, tool):
    if not _is_tool_admin(user, tool):
        raise HTTPException(403, f"admin tier on {tool['slug']} required (bh share {tool['slug']} <you> --tier admin)")


def _invite_url(ws, token: str) -> str:
    return f"https://{config.PLATFORM_DOMAIN}/join/{token}"


def _member_sign_in_url(ws) -> str:
    return f"https://{config.PLATFORM_DOMAIN}/login?next=" + quote(f"/welcome?ws={ws['slug']}&joined=1", safe="")


def _existing_member_notice(ws, email: str, by: str) -> tuple[str, bool]:
    url = _member_sign_in_url(ws)
    emailed = mail.send(email, f"You have access to {ws['name']} on Boat House",
                        f"{by} added you to {ws['name']}. Open your workspace with your usual Boat House password:\n\n{url}\n\n"
                        f"Forgot your password? Reset it at https://{config.PLATFORM_DOMAIN}/forgot-password\n", reply_to=by if "@" in by else None)
    return url, emailed


# =============================================================================
# Sign-in (auth.<base>) and the workspace home (<base>)
# =============================================================================

@app.get("/login")
def login_form(request: Request, next: str | None = None):
    w = _where(request)
    if w.kind == "platform":
        return front.login_form(request, next)
    if w.kind not in ("auth", "home") or not w.workspace:
        return HTMLResponse(pages.unknown(_host(request)), 404)
    if auth.session_user(request.cookies.get(config.SESSION_COOKIE), w.workspace["id"]):
        return RedirectResponse(_safe_next(next, w.base), 302)
    return HTMLResponse(pages.login(w.base, _safe_next(next, w.base), auth.csrf_token("login")))


@app.post("/login")
def login_submit(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form(""), csrf: str = Form("")):
    w = _where(request)
    if w.kind == "platform":
        return front.login_submit(request, email, password, next, csrf)
    if w.kind != "auth" or not w.workspace:
        return HTMLResponse(pages.unknown(_host(request)), 404)
    email = email.strip().lower()
    nxt = _safe_next(next, w.base)
    key = f"{_ip(request)}|{email}"

    def again(msg, code=400):
        return HTMLResponse(pages.login(w.base, nxt, auth.csrf_token("login"), msg, email), code)

    if not auth.csrf_ok(csrf, "login"):
        return again("That form had expired, or its token did not match (a copy from another tab?). Load the page again and retry.")
    if auth.throttle(key):
        db.audit(email, "login.throttled", None, {"ip": _ip(request)}, w.workspace["id"])
        return again("Too many attempts. Wait fifteen minutes.", 429)
    user = _user(w.workspace["id"], email)
    if not user or not auth.check_login(email, password):
        auth.record_attempt(key)
        db.audit(email, "login.failed", None, {"ip": _ip(request)}, w.workspace["id"])
        return again("That email and password do not match." + (" You have no password yet; open your invite link." if auth.needs_invite(user) else ""), 401)
    auth.touch_login(email)
    sid = auth.create_session(w.workspace["id"], email, user["name"])
    db.audit(email, "login.ok", None, {"ip": _ip(request)}, w.workspace["id"])
    resp = RedirectResponse(nxt, status_code=303)
    _set_session(resp, w, sid)
    return resp


def _join_where(request: Request, inv):
    """An invite opens on the platform host (any workspace) or on that workspace's own auth host."""
    w = _where(request)
    if not inv:
        return None
    if w.kind == "platform":
        return w
    if w.kind == "auth" and w.workspace and inv["workspace_id"] == w.workspace["id"]:
        return w
    return None


@app.get("/join/{token}")
def join_form(token: str, request: Request, verify: str = ""):
    inv = auth.invite_for(token, include_inactive=True)
    w = _join_where(request, inv)
    if not w:
        return HTMLResponse(pages.invite_dead(), 404)
    user = _user(inv["workspace_id"], inv["email"])
    if not user:
        return HTMLResponse(pages.invite_dead(), 404)
    if auth.has_password(inv["email"]):
        return RedirectResponse(_member_sign_in_url(db.workspace_by_id(inv["workspace_id"])), 303)
    if inv["used"] is not None or inv["expires"] <= time.time():
        return HTMLResponse(pages.invite_email(inv["email"], auth.csrf_token("join"), "This invitation expired. We can email you a fresh secure link."))
    if not auth.invite_proof_ok(inv, verify):
        return HTMLResponse(pages.invite_email(inv["email"], auth.csrf_token("join")))
    where = config.PLATFORM_DOMAIN if w.kind == "platform" else w.base
    return HTMLResponse(pages.join(inv["email"], where, auth.csrf_token("join"), name=(user["name"] if user else "") or ""))


@app.post("/join/{token}")
def join_submit(token: str, request: Request, name: str = Form(""), password: str = Form(""), password2: str = Form(""), csrf: str = Form(""), verify: str = ""):
    inv = auth.invite_for(token, include_inactive=True)
    w = _join_where(request, inv)
    if not w:
        return HTMLResponse(pages.invite_dead(), 404)
    if not _user(inv["workspace_id"], inv["email"]):
        return HTMLResponse(pages.invite_dead(), 404)
    if auth.has_password(inv["email"]):
        return RedirectResponse(_member_sign_in_url(db.workspace_by_id(inv["workspace_id"])), 303)
    where = config.PLATFORM_DOMAIN if w.kind == "platform" else w.base
    if not auth.csrf_ok(csrf, "join"):
        return HTMLResponse(pages.join(inv["email"], where, auth.csrf_token("join"), "That form had expired, or its token did not match (a copy from another tab?). Load the page again and retry.", name), 400)
    if inv["used"] is not None or inv["expires"] <= time.time() or not auth.invite_proof_ok(inv, verify):
        rate = f"invite-mail|{inv['email']}"
        if auth.throttle(rate, limit=3):
            return HTMLResponse(pages.invite_email(inv["email"], auth.csrf_token("join"), "Please wait fifteen minutes before trying again."), 429)
        auth.record_attempt(rate)
        ws = db.workspace_by_id(inv["workspace_id"])
        user = _user(inv["workspace_id"], inv["email"])
        if not user:
            return HTMLResponse(pages.invite_dead(), 404)
        if inv["used"] is not None or inv["expires"] <= time.time():
            token = auth.create_invite(ws["id"], inv["email"], inv["created_by"])
        sent = mail.invite_notice(inv["email"], inv["created_by"] or "The workspace owner", ws["name"], user["role"], _invite_url(ws, token))
        return HTMLResponse(pages.invite_email(inv["email"], auth.csrf_token("join"),
                            "Check your inbox and spam folder for your secure sign-in link." if sent else
                            "We could not send the email. Please contact Boat House support at support@example.com.", sent=sent), 200 if sent else 503)
    problem = auth.password_problem(password) or ("the two passwords differ" if password != password2 else None)
    if problem:
        return HTMLResponse(pages.join(inv["email"], where, auth.csrf_token("join"), problem, name), 400)
    if not _user(inv["workspace_id"], inv["email"]):
        return HTMLResponse(pages.invite_dead(), 404)
    nm = name.strip()[:80] or None
    if not auth.accept_invite(inv, password, nm, verify):
        if auth.has_password(inv["email"]):
            return RedirectResponse(_member_sign_in_url(db.workspace_by_id(inv["workspace_id"])), 303)
        return HTMLResponse(pages.invite_dead(), 404)
    db.audit(inv["email"], "invite.accepted", None, {"ip": _ip(request), "on": w.kind}, inv["workspace_id"])
    ws = db.workspace_by_id(inv["workspace_id"])
    if w.kind == "platform":
        # the person has a password now; give them the welcome page for this workspace, with a key that is them everywhere
        return front._to_welcome(inv["email"], ws["slug"], name="first key", joined=True)
    sid = auth.create_session(inv["workspace_id"], inv["email"], nm)
    resp = RedirectResponse(f"https://{w.base}/", status_code=303)
    _set_session(resp, w, sid)
    return resp


@app.get("/forgot-password")
def forgot_password_form(request: Request):
    if _where(request).kind != "platform":
        return RedirectResponse(f"https://{config.PLATFORM_DOMAIN}/forgot-password", 303)
    return HTMLResponse(pages.forgot_password(auth.csrf_token("forgot-password")))


@app.post("/forgot-password")
def forgot_password_submit(request: Request, email: str = Form(""), csrf: str = Form("")):
    if _where(request).kind != "platform":
        raise HTTPException(404)
    def again(message: str, status: int = 400):
        return HTMLResponse(pages.forgot_password(auth.csrf_token("forgot-password"), message), status)
    if not auth.csrf_ok(csrf, "forgot-password"):
        return again("This form expired. Please try again.")
    email = email.strip().lower()
    ip_limit, email_limit = f"recover|{_ip(request)}", f"recover-email|{email}"
    if auth.throttle(ip_limit, limit=8) or auth.throttle(email_limit, limit=3):
        return again("Please wait fifteen minutes before requesting another email.", 429)
    auth.record_attempt(ip_limit)
    auth.record_attempt(email_limit)
    if not mail.configured():
        return again("Password recovery email is temporarily unavailable. Contact Boat House support at support@example.com.", 503)
    token = auth.create_password_reset(email)
    if token:
        url = f"https://{config.PLATFORM_DOMAIN}/reset-password/{token}"
        sent = mail.send(email, "Reset your Boat House password",
                         f"Open this link to choose a new Boat House password:\n\n{url}\n\n"
                         "It works once and expires in thirty minutes. If you did not ask for this, you can ignore this email.\n")
        if not sent:
            return again("We could not send the recovery email. Please try again later or contact Boat House support at support@example.com.", 503)
    return HTMLResponse(pages.forgot_password(auth.csrf_token("forgot-password"),
                        "If that email has a Boat House account, a password reset link is on its way. Check your inbox and spam folder.", sent=True))


@app.get("/reset-password/{token}")
def reset_password_form(token: str, request: Request):
    if _where(request).kind != "platform":
        raise HTTPException(404)
    if not auth.password_reset_for(token):
        return HTMLResponse(pages.password_reset_expired(), 410)
    return HTMLResponse(pages.reset_password(auth.csrf_token("reset-password")))


@app.post("/reset-password/{token}")
def reset_password_submit(token: str, request: Request, password: str = Form(""), password2: str = Form(""), csrf: str = Form("")):
    if _where(request).kind != "platform":
        raise HTTPException(404)
    if not auth.password_reset_for(token):
        return HTMLResponse(pages.password_reset_expired(), 410)
    if not auth.csrf_ok(csrf, "reset-password"):
        return HTMLResponse(pages.reset_password(auth.csrf_token("reset-password"), "This form expired. Please try again."), 400)
    problem = auth.password_problem(password) or ("The two passwords differ." if password != password2 else None)
    if problem:
        return HTMLResponse(pages.reset_password(auth.csrf_token("reset-password"), problem), 400)
    email = auth.reset_password(token, password)
    if not email:
        return HTMLResponse(pages.password_reset_expired(), 410)
    db.audit(email, "password.recovered", None, {"ip": _ip(request)})
    return front._signed_in(email)


@app.get("/handoff")
def handoff(request: Request, t: str = ""):
    """Someone who just set their password on the platform host arrives here on the workspace's own sign-in host
    with a two-minute signed token; they get the workspace session and land on the tool list. One sign-in, not two."""
    w = _where(request)
    p = auth.verify(t)
    if w.kind != "auth" or not w.workspace or not p or p.get("handoff") != w.workspace["id"] or not p.get("n"):
        return HTMLResponse(pages.invite_dead(), 404)
    if not p.get("pw") or p["pw"] != auth.credential_stamp(p["email"]):
        return HTMLResponse(pages.invite_dead(), 404)
    if not auth.spend_token(t, p.get("exp")):          # a handoff link works exactly once
        return HTMLResponse(pages.invite_dead(), 404)
    user = _user(w.workspace["id"], p["email"])
    if not user:
        return HTMLResponse(pages.invite_dead(), 404)
    sid = auth.create_session(w.workspace["id"], p["email"], user["name"] or p.get("name"))
    db.audit(p["email"], "login.handoff", None, {"ip": _ip(request)}, w.workspace["id"])
    resp = RedirectResponse(_safe_next(p.get("to"), w.base), status_code=303)
    _set_session(resp, w, sid)
    return resp


@app.get("/logout")
def logout(request: Request):
    w = _where(request)
    if w.kind == "platform":
        return front.platform_logout(request)
    auth.delete_session(request.cookies.get(config.SESSION_COOKIE))
    resp = RedirectResponse(f"https://{w.base}/" if w.base else "/", status_code=302)
    if w.base:
        resp.delete_cookie(config.SESSION_COOKIE, domain=w.cookie_domain, path="/")
    return resp


@app.get("/me")
def me(request: Request):
    w = _where(request)
    if not w.workspace:
        raise HTTPException(404)
    user = auth.session_user(request.cookies.get(config.SESSION_COOKIE), w.workspace["id"] if w.workspace else None)
    if not user:
        raise HTTPException(401)
    return {"email": user["email"], "name": user["name"], "role": user["role"], "workspace": w.workspace["slug"]}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    w = _where(request)
    if w.kind in ("platform", "api", "mcp"):
        return front.landing(request)
    if w.kind == "auth":
        return RedirectResponse(f"https://{w.base}/", 302)
    if w.kind != "home":
        return HTMLResponse(pages.unknown(_host(request)), 404)
    user = auth.session_user(request.cookies.get(config.SESSION_COOKIE), w.workspace["id"])
    if not user:
        return RedirectResponse(f"https://{w.auth_host}/login?next={quote('https://' + w.base + '/', safe='')}", 302)
    tools = []
    with db.conn() as c:
        for t in c.execute("SELECT * FROM tools WHERE workspace_id=? ORDER BY name", (user["workspace_id"],)):
            tier = _tier_for(user, t)
            if tier:
                tools.append({"slug": t["slug"], "name": t["name"], "role": tier[0], "state": deploy.status(t)["state"]})
    return HTMLResponse(pages.home(user, tools, w.base))


# =============================================================================
# Caddy-facing internals
# =============================================================================

def _app_cookie(request: Request) -> str:
    """Pass application cookies through while keeping workspace/account sessions at the gateway."""
    reserved = {config.SESSION_COOKIE, config.PLATFORM_COOKIE}
    return "; ".join(part.strip() for header in request.headers.getlist("cookie") for part in header.split(";")
                     if "=" in part and part.split("=", 1)[0].strip() not in reserved)

@app.get("/internal/authz")
def authz(request: Request):
    host = _host(request).lower()
    uri = request.headers.get("x-forwarded-uri") or "/"
    w = hosts.resolve(host)
    if uri.startswith("/internal"):
        return HTMLResponse(pages.unknown(host), 404)
    if w.kind in ("platform", "api", "mcp", "auth", "home"):
        return Response(status_code=200, headers={"X-Boathouse-Upstream": "control:8000"})
    tool = hosts.tool_for(w)
    if not tool:
        return HTMLResponse(pages.unknown(host), 404)
    if resources.blocked(tool):
        return HTMLResponse(pages.page('App paused','<h1>This app is at its capacity limit.</h1><p>The owner can review capacity in Boat House. Existing data is kept.</p>'),503)
    if w.workspace["paused"]:
        return HTMLResponse(pages.paused(host, _owners(w.workspace["id"])), 402)
    user = auth.session_user(request.cookies.get(config.SESSION_COOKIE), w.workspace["id"])
    method = (request.headers.get("x-forwarded-method") or "GET").upper()
    public = bool(tool["public"]) if "public" in tool.keys() else False
    if not user:
        if public and method in SAFE_METHODS:
            # a public tool: anyone may read it; the tool sees an empty, signed, viewer identity
            ts = str(int(time.time()))
            return Response(status_code=200, headers={
                "X-Boathouse-App-Cookie": _app_cookie(request),
                "X-Boathouse-User": "", "X-Boathouse-Name": "", "X-Boathouse-Tier": "viewer", "X-Boathouse-Labels": "",
                "X-Boathouse-Role": "viewer", "X-Boathouse-Tool": w.slug, "X-Boathouse-Workspace": w.workspace["slug"],
                "X-Boathouse-Ts": ts, "X-Boathouse-Sig": auth.sign_identity(tool["signing_key"], "", "viewer", "", w.slug, ts),
                "X-Boathouse-Upstream": f"{deploy.ctr_name(tool)}:{config.TOOL_PORT}"})
        return RedirectResponse(f"https://{w.auth_host}/login?next={quote('https://' + host + uri, safe='')}", status_code=302)
    t = _tier_for(user, tool)
    if not t and public:
        t = ("viewer", "")
    if not t:
        db.audit(user["email"], "access.denied", w.slug, None, w.workspace["id"])
        return HTMLResponse(pages.denied(user["email"], host, _owners(w.workspace["id"]), w.auth_host), 403)
    tier, labels = t
    if tier == "viewer" and method not in SAFE_METHODS:
        # the blunt safety net: a viewer's browser cannot write, whatever the tool does
        return HTMLResponse(pages.view_only(host), 403)
    ts = str(int(time.time()))
    headers = {
        "X-Boathouse-App-Cookie": _app_cookie(request),
        "X-Boathouse-User": user["email"],
        "X-Boathouse-Name": user["name"] or "",
        "X-Boathouse-Tier": tier,
        "X-Boathouse-Labels": labels,
        "X-Boathouse-Role": tier,
        "X-Boathouse-Tool": w.slug,
        "X-Boathouse-Workspace": w.workspace["slug"],
        "X-Boathouse-Ts": ts,
        "X-Boathouse-Sig": auth.sign_identity(tool["signing_key"], user["email"], tier, labels, w.slug, ts),
        "X-Boathouse-Upstream": f"{deploy.ctr_name(tool)}:{config.TOOL_PORT}",
    }
    return Response(status_code=200, headers=headers)


@app.get("/internal/tls-ask")
def tls_ask(domain: str = ""):
    w = hosts.resolve(domain)
    if w.kind in ("platform", "api", "mcp", "auth", "home"):
        return PlainTextResponse("ok")
    if w.kind == "tool" and hosts.tool_for(w):
        return PlainTextResponse("ok")
    raise HTTPException(404)


@app.get("/internal/down", response_class=HTMLResponse)
def down(host: str = ""):
    return HTMLResponse(pages.down(host), 503)


# =============================================================================
# API for the CLI and agents
# =============================================================================

def _actor(request: Request, authorization: str | None):
    """A project key (agents, CLI) or a browser session on this workspace's auth host."""
    user = None
    want = (request.headers.get("boathouse-workspace") or request.headers.get("x-boathouse-workspace") or "").strip().lower() or None
    wid = None
    if want:
        target = db.workspace(want)
        if not target:
            raise HTTPException(404, f"no workspace called {want}")
        wid = target["id"]
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
        user = auth.key_user(bearer, wid)
        if not user and auth.key_row(bearer):
            raise HTTPException(403, f"that key does not work in workspace {want}" if want else "that key belongs to nobody in any workspace")
    if not user:
        w = _where(request)
        if w.workspace:
            user = auth.session_user(request.cookies.get(config.SESSION_COOKIE), w.workspace["id"])
            if user and request.method not in SAFE_METHODS and not _same_origin(request, request.headers.get("origin")):
                raise HTTPException(403, "browser API changes must originate on this workspace's sign-in host; agents should use a project key")
    if not user:
        raise HTTPException(401, "a valid project key or workspace sign-in is required")
    return user   # guests get in too; every workspace-level action checks _require_owner / _require_member, every tool action its tier


def _require_member(user):
    if user["role"] not in ("owner", "member"):
        raise HTTPException(403, "workspace owners and members only; a person shared in as a guest can work on the tools shared with them, not make new ones")


def _ws(user):
    return db.workspace_by_id(user["workspace_id"])


def _require_owner(user):
    if user["role"] != "owner":
        raise HTTPException(403, "workspace owners only")


def _tool_or_404(wid: str, slug: str):
    t = _tool_by_slug(wid, slug)
    if not t:
        raise HTTPException(404, f"no tool named {slug}")
    return t


def _public_tool(t, ws, user=None) -> dict:
    st = deploy.status(t)
    with db.conn() as c:
        rel = c.execute("SELECT seq, created, created_by, note, source FROM releases WHERE id=?", (t["current_release_id"],)).fetchone()
        grants = [dict(g) for g in c.execute("SELECT email, tier, labels FROM grants WHERE tool_id=? ORDER BY email", (t["id"],))]
    base = hosts.base_for(ws)
    mine = _tier_for(user, t) if user is not None else None
    front = hosts.front_domain(ws["id"], t["slug"])
    return {"slug": t["slug"], "name": t["name"], "url": hosts.tool_url(ws, t["slug"]),
            "urls": ([f"https://{front['domain']}", f"https://www.{front['domain']}"] if front else []) + [f"https://{t['slug']}.{b}" for b in hosts.all_bases(ws)],
            "public": bool(t["public"]) if "public" in t.keys() else False,
            "default_access": t["default_access"], "default_tier": t["default_tier"],
            "usage": resources.current(t) if config.RESOURCE_GUARD else None, "status": st, "release": {k: rel[k] for k in ("seq", "created", "created_by", "note")} | {"has_source": bool(rel["source"])} if rel else None,
            "grants": grants, "created_by": t["created_by"], "my_tier": mine[0] if mine else None}


@app.get("/api/whoami")
def whoami(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    return {"email": u["email"], "role": u["role"], "workspace": ws["slug"], "domain": hosts.base_for(ws),
            "domains": hosts.all_bases(ws), "platform": config.PLATFORM_DOMAIN,
            "balance_cents": (billing.balance(ws["id"]) if u["role"] == "owner" else None), "paused": bool(ws["paused"]), "host": ws["slug"] == config.HOST_WORKSPACE}


@app.get("/api/me")
def me(request: Request, authorization: str | None = Header(None)):
    """The person behind a key: every workspace they belong to, and whether the key is account-wide."""
    u = _actor(request, authorization)
    k = auth.key_row(authorization.split(" ", 1)[1].strip()) if authorization and authorization.lower().startswith("bearer ") else None
    return {"email": u["email"], "account_key": bool(k and k["workspace_id"] == auth.ACCOUNT),
            "workspaces": [{"slug": m["slug"], "name": m["name"], "role": m["my_role"], "domain": hosts.base_for(m)} for m in auth.memberships(u["email"])]}


@app.get("/api/tools")
def list_tools(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    with db.conn() as c:
        rows = c.execute("SELECT * FROM tools WHERE workspace_id=? ORDER BY slug", (ws["id"],)).fetchall()
    if u["role"] not in ("owner", "member"):
        rows = [t for t in rows if _tier_for(u, t)]          # a guest sees only what was shared with them
    return [_public_tool(t, ws, u) for t in rows]


@app.get('/api/tools/{slug}/usage')
def tool_usage(slug: str,request: Request,authorization: str | None = Header(None)):
    u=_actor(request,authorization);ws=_ws(u);t=_tool_or_404(ws['id'],slug)
    _require_tool_admin(u,t)
    return resources.current(t)

@app.post('/api/tools/{slug}/capacity')
def tool_capacity(slug: str,request: Request,body: dict,authorization: str | None = Header(None)):
    u=_actor(request,authorization);_require_owner(u);ws=_ws(u);t=_tool_or_404(ws['id'],slug)
    if type(body.get('confirm',False)) is not bool: raise HTTPException(422,'confirm must be true or false')
    try:
        if body.get('confirm'):
            return resources.confirm(t,ws,body.get('quote_id'),body.get('max_extra_monthly_cents'),u['email'])
        return resources.quote(t,ws,body.get('storage_gb'),u['email'])
    except resources.ResourceError as e: raise HTTPException(409,str(e))


@app.post("/api/tools")
def create_tool(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_member(u)
    ws = _ws(u)
    slug = (body.get("slug") or "").lower()
    if slug == "www":
        raise HTTPException(422, "www is not a tool name: to put a tool on the front of a domain (the apex and www), "
                                 "run bh domain point <domain> <tool>, or bh domain buy <domain> --to <tool>")
    if not SLUG_RE.match(slug) or slug in RESERVED:
        raise HTTPException(422, "slug must be 2-31 chars of a-z, 0-9, '-' and not reserved")
    if _tool_by_slug(ws["id"], slug):
        raise HTTPException(409, "tool exists")
    tier = body.get("default_tier", "viewer")
    if tier not in TIERS:
        raise HTTPException(422, "default_tier must be viewer, editor or admin")
    if body.get("default_access", "members") not in ("members", "listed"):
        raise HTTPException(422, "default_access must be members or listed")
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM tools WHERE workspace_id=? AND slug=?", (ws["id"], slug)).fetchone():
            raise HTTPException(409, "tool exists")
        kept = c.execute("SELECT * FROM tool_keepsakes WHERE workspace_id=? AND slug=?", (ws["id"], slug)).fetchone()
        c.execute("""INSERT INTO tools (id, workspace_id, slug, name, default_access, default_role, current_release_id,
                     signing_key, db_password, created, created_by, resource_key, default_tier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (db.new_id("t"), ws["id"], slug, body.get("name") or slug,
                   body.get("default_access", "members"), "viewer", None,
                   (kept["signing_key"] if kept else None) or secrets.token_hex(32),
                   (kept["db_password"] if kept else None) or secrets.token_urlsafe(24), time.time(), u["email"],
                   kept["resource_key"] if kept else None, tier))
        t = c.execute("SELECT * FROM tools WHERE workspace_id=? AND slug=?", (ws["id"], slug)).fetchone()
        # whoever creates a tool is its admin, whatever their workspace role
        c.execute("INSERT INTO grants (id, tool_id, email, role, created, created_by, tier, labels) VALUES (?,?,?,?,?,?,?,?)",
                  (db.new_id("g"), t["id"], u["email"], "admin", time.time(), u["email"], "admin", ""))
        revived = _revive_tool(c, ws["id"], t)
        c.execute("COMMIT")
    db.audit(u["email"], "tool.create", slug, revived or None, ws["id"])
    return _public_tool(_tool_by_slug(ws["id"], slug), ws, u)


def _keep_tool(c, wid: str, t) -> None:
    """What comes back with the data when a tool of this name is deployed again: who it was shared with, and its secrets."""
    grants = [dict(r) for r in c.execute("SELECT email, role, tier, labels, created_by FROM grants WHERE tool_id=?", (t["id"],))]
    kept = [dict(r) for r in c.execute("SELECT name, value_enc, updated_by FROM secrets WHERE tool_id=?", (t["id"],))]
    c.execute("""INSERT OR REPLACE INTO tool_keepsakes
                 (workspace_id, slug, grants, secrets, saved, resource_key, db_password, signing_key) VALUES (?,?,?,?,?,?,?,?)""",
              (wid, t["slug"], json.dumps(grants), json.dumps(kept), time.time(), t["resource_key"], t["db_password"], t["signing_key"]))


def _revive_tool(c, wid: str, t) -> dict:
    """Put a deleted tool's sharing and secrets back on its successor of the same name."""
    k = c.execute("SELECT * FROM tool_keepsakes WHERE workspace_id=? AND slug=?", (wid, t["slug"])).fetchone()
    if not k:
        return {}
    grants, kept = json.loads(k["grants"]), json.loads(k["secrets"])
    for g in grants:
        c.execute("""INSERT INTO grants (id, tool_id, email, role, created, created_by, tier, labels) VALUES (?,?,?,?,?,?,?,?)
                     ON CONFLICT(tool_id, email) DO NOTHING""",
                  (db.new_id("g"), t["id"], g["email"], g["role"], time.time(), g.get("created_by"), g.get("tier") or g["role"], g.get("labels") or ""))
    for s in kept:
        c.execute("INSERT OR REPLACE INTO secrets (tool_id, name, value_enc, updated, updated_by) VALUES (?,?,?,?,?)",
                  (t["id"], s["name"], s["value_enc"], time.time(), s.get("updated_by")))
    c.execute("DELETE FROM tool_keepsakes WHERE workspace_id=? AND slug=?", (wid, t["slug"]))
    return {"grants": len(grants), "secrets": len(kept)}


@app.get("/api/tools/{slug}")
def get_tool(slug: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_or_404(ws["id"], slug)
    if u["role"] not in ("owner", "member") and not _tier_for(u, t):
        raise HTTPException(404, "no such tool")
    ws = _ws(u)
    return _public_tool(_tool_or_404(ws["id"], slug), ws, u)


@app.patch("/api/tools/{slug}")
def patch_tool(slug: str, request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_or_404(ws["id"], slug)
    _require_tool_admin(u, t)
    fields = {k: v for k, v in body.items() if k in ("name", "default_access", "default_tier")}
    if "public" in body:
        fields["public"] = 1 if body["public"] else 0
    if "default_access" in fields and fields["default_access"] not in ("members", "listed"):
        raise HTTPException(422, "default_access must be members or listed")
    if "default_tier" in fields and fields["default_tier"] not in TIERS:
        raise HTTPException(422, "default_tier must be viewer, editor or admin")
    with db.conn() as c:
        for k, v in fields.items():
            c.execute(f"UPDATE tools SET {k}=? WHERE id=?", (v, t["id"]))
    db.audit(u["email"], "tool.update", slug, fields, ws["id"])
    return _public_tool(_tool_or_404(ws["id"], slug), ws, u)


@app.delete("/api/tools/{slug}")
async def delete_tool(slug: str, request: Request, purge: bool = False, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_or_404(ws["id"], slug)
    _require_tool_admin(u, t)
    if purge:
        _require_owner(u)
    await run_in_threadpool(_delete_tool_locked, t, ws, purge)
    db.audit(u["email"], "tool.delete", slug, {"purge": purge}, ws["id"])
    return {"deleted": slug, "data_purged": purge}


def _delete_tool_locked(t, ws, purge):
    with deploy.operation(t):
        with db.conn() as c:
            t = c.execute("SELECT * FROM tools WHERE id=?", (t["id"],)).fetchone()
        if t is None:
            raise HTTPException(404, "tool was already deleted")
        deploy.remove(t, purge)
        if purge:
            source_dir = config.SOURCE_DIR / t["id"]
            if source_dir.exists():
                shutil.rmtree(source_dir)
        with db.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if purge:
                c.execute("DELETE FROM tool_keepsakes WHERE workspace_id=? AND slug=?", (ws["id"], t["slug"]))
            else:
                _keep_tool(c, ws["id"], t)
            c.execute("DELETE FROM grants WHERE tool_id=?", (t["id"],))
            c.execute("DELETE FROM secrets WHERE tool_id=?", (t["id"],))
            c.execute("DELETE FROM releases WHERE tool_id=?", (t["id"],))
            c.execute("DELETE FROM tools WHERE id=?", (t["id"],))
            c.execute("COMMIT")


def _source_path(tool, seq: int):
    return config.SOURCE_DIR / tool["id"] / f"{seq}.tar.gz"


def _do_release(t, ws, u, context: bytes | None, note: str | None, image: str | None,
                source: str | None = None, base: int | None = None):
    """Serialize on durable resource identity, then recheck the submitted base."""
    with deploy.operation(t), resources.host_operation():
        resources.admission(t)
        return _release_locked(t, ws, u, context, note, image, source, base)


def _release_locked(t, ws, u, context, note, image, source, base):
    with db.conn() as c:
        t = c.execute("SELECT * FROM tools WHERE id=?", (t["id"],)).fetchone()
        if t is None:
            raise HTTPException(404, "tool was deleted while this operation was waiting")
        cur = c.execute("SELECT seq FROM releases WHERE id=?", (t["current_release_id"],)).fetchone()
        current_seq = cur["seq"] if cur else 0
        if base is not None and base != current_seq:
            raise HTTPException(409, {"error": f"your copy is based on release #{base} but #{current_seq} is live; pull {t['slug']} and reapply your change",
                                      "code": "STALE_BASE", "base": base, "current": current_seq})
        c.execute("BEGIN IMMEDIATE")
        seq = (c.execute("SELECT COALESCE(MAX(seq),0) FROM releases WHERE tool_id=?", (t["id"],)).fetchone()[0] or 0) + 1
        rid = db.new_id("r")
        c.execute("INSERT INTO releases (id, tool_id, seq, image, status, note, log, created, created_by, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (rid, t["id"], seq, image or "", "building", note, None, time.time(), u["email"], source))
        c.execute("COMMIT")
    log = ""
    try:
        if context is not None:
            sp = _source_path(t, seq)
            sp.parent.mkdir(parents=True, exist_ok=True)
            if sum(p.stat().st_size for p in sp.parent.glob('*.tar.gz'))+len(context)>config.MAX_SOURCE_HISTORY_BYTES:
                raise ValueError('This app reached its 512 MB source-history limit. Contact support to archive old releases before publishing more.')
            with sp.open("xb") as archive:
                archive.write(context)
            with db.conn() as c:
                c.execute("UPDATE releases SET source=? WHERE id=?", (str(sp), rid))
            image, log = deploy.build(t, seq, context)
        with db.conn() as c:
            c.execute("UPDATE releases SET image=?, status='starting', log=? WHERE id=?", (image, log, rid))
        resources.admission(t)
        deploy.run(t, image, ws)
        with db.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE releases SET status='live' WHERE id=?", (rid,))
            c.execute("UPDATE tools SET current_release_id=? WHERE id=?", (rid, t["id"]))
            keep = {r["image"] for r in c.execute(
                "SELECT image FROM releases WHERE tool_id=? AND status IN ('live','superseded') ORDER BY seq DESC LIMIT ?",
                (t["id"], config.KEEP_RELEASES))}
            c.execute("UPDATE releases SET status='superseded' WHERE tool_id=? AND status='live' AND id<>?", (t["id"], rid))
            c.execute("COMMIT")
        try:
            deploy.prune_images(t, keep)
        except Exception as e:
            db.audit("deploy", "images.prune_failed", t["slug"], {"error": str(e)[:200]}, ws["id"])
        return {"release": seq, "image": image, "url": hosts.tool_url(ws, t["slug"]), "log_tail": log[-2000:]}
    except Exception as e:  # noqa: BLE001
        err = deploy.collapse_repeats(str(e))
        cause = deploy.cause_line(err)
        code = "BUILD_FAILED" if (context is not None and not log) else "START_FAILED"
        with db.conn() as c:
            c.execute("UPDATE releases SET status='failed', log=? WHERE id=?", (f"{log}\n{err}" if log else err, rid))
            live = c.execute("SELECT seq FROM releases WHERE id=? AND status='live'", (t["current_release_id"],)).fetchone()
        live_seq = live["seq"] if live else None
        text = ((f"the build failed: {cause}" if code == "BUILD_FAILED" else f"the tool did not start: {cause}") +
                (f"\nRelease #{live_seq} is still live; nothing changed for the people using it. Your next deploy keeps base {live_seq}."
                 if live_seq else "\nNothing is live for this tool yet.") + f"\n{err}")
        raise HTTPException(422, {"release": seq, "code": code, "cause": cause, "live_release": live_seq, "base": live_seq,
                                  "error": text[-3000:]})


def _months_words(cents: int, day: int) -> str:
    n = max(1, cents // (day * 30))
    return {1: "one month", 2: "two months", 3: "three months", 4: "four months", 5: "five months", 6: "six months"}.get(n, f"{n} months")


def _deploy_gate(ws) -> str | None:
    """Why a deploy would be refused for money right now, in words, or None. The deploy route raises it as a
    402; bh and the MCP list ask for it first, so nobody uploads a folder only to be told the balance is empty."""
    day = billing.daily_rate(ws)
    bal = billing.balance(ws["id"])
    if bal < day and not (ws["stripe_pm"] and ws["autorefill_cents"]):
        return (f"put money on the balance before deploying: a running tool costs ${day/100:.2f} a day and the balance is "
                f"${bal/100:.2f}. The owner adds money at https://{config.PLATFORM_DOMAIN}/account?ws={ws['slug']} "
                f"(or ask your agent for a secure payment link). $20 covers about {2000//billing.monthly_rate(ws)} months of one tool at the current rate.")
    return None


@app.get("/api/billing/deploy-check")
def deploy_check(request: Request, tool: str | None = None, authorization: str | None = Header(None)):
    """Would a deploy of this tool be refused, for money or for standing? Asked before uploading anything."""
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_by_slug(ws["id"], tool.lower()) if tool else None
    if u["role"] not in ("owner", "member"):
        if not t:
            return {"ok": False, "code": 403, "guest": True,
                    "message": "you are a guest in this workspace: you can work on the tools shared with you, not make new ones here"}
        tier = _tier_for(u, t)
        if not tier or tier[0] != "admin":
            return {"ok": False, "code": 403, "guest": True,
                    "message": f"you are {tier[0] if tier else 'not shared in'} on {t['slug']}; only its admins can change the software"}
    why = _deploy_gate(ws)
    return {"ok": why is None, "code": 402 if why else None, "message": why}


async def _read_context(context: UploadFile) -> bytes:
    """Read at most the context limit plus one byte; never allocate an unbounded upload."""
    if context.size is not None and context.size > MAX_CONTEXT_BYTES:
        raise HTTPException(413, "upload over 200 MB; add a .dockerignore")
    data = io.BytesIO()
    size = 0
    while True:
        chunk = await context.read(min(1024 * 1024, MAX_CONTEXT_BYTES - size + 1))
        if not chunk:
            return data.getvalue()
        size += len(chunk)
        if size > MAX_CONTEXT_BYTES:
            raise HTTPException(413, "upload over 200 MB; add a .dockerignore")
        data.write(chunk)


@app.post("/api/tools/{slug}/deploys")
async def deploy_tool(slug: str, request: Request, context: UploadFile = File(...), note: str | None = Form(None),
                      name: str | None = Form(None), base: int | None = Form(None), authorization: str | None = Header(None)):
    """Push a folder. `base` is the release the folder was pulled from; a stale
    base is refused so two admins cannot silently overwrite each other."""
    u = _actor(request, authorization)
    ws = _ws(u)
    why = _deploy_gate(ws)
    if why:
        raise HTTPException(402, why)
    t = _tool_by_slug(ws["id"], slug)
    if not t:
        _require_member(u)
    else:
        _require_tool_admin(u, t)
    data = await _read_context(context)
    if not t:
        create_tool(request, {"slug": slug, "name": name or slug}, authorization)
        t = _tool_by_slug(ws["id"], slug)
    lock = _deploy_locks.setdefault(t["id"], asyncio.Lock())
    async with lock:
        result = await run_in_threadpool(_do_release, t, ws, u, data, note, None, None, base)
    db.audit(u["email"], "tool.deploy", slug, {"release": result["release"]}, ws["id"])
    return result


@app.post("/api/tools/{slug}/rollback")
async def rollback(slug: str, request: Request, body: dict | None = None, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_or_404(ws["id"], slug)
    _require_tool_admin(u, t)
    target = (body or {}).get("to")
    with db.conn() as c:
        rows = c.execute("SELECT * FROM releases WHERE tool_id=? ORDER BY seq DESC", (t["id"],)).fetchall()
    runnable = [x for x in rows if x["status"] in ("live", "superseded") and x["image"]]
    if target:
        r = next((x for x in rows if x["seq"] == int(target)), None)
        if r is None:
            raise HTTPException(404, f"there is no release #{int(target)} of {slug}")
        if r not in runnable:
            cands = ", ".join(f"#{x['seq']}" for x in runnable) or "none yet"
            raise HTTPException(409, f"release #{r['seq']} never ran (its build or start failed), so it cannot be put back; "
                                     f"nothing was changed. Releases that can: {cands}.")
    else:
        r = next((x for x in runnable if x["status"] == "superseded"), None)
        if not r:
            raise HTTPException(404, "no earlier release to roll back to")
    lock = _deploy_locks.setdefault(t["id"], asyncio.Lock())
    async with lock:
        result = await run_in_threadpool(_do_release, t, ws, u, None, f"rollback to #{r['seq']}", r["image"], r["source"])
    db.audit(u["email"], "tool.rollback", slug, {"to": r["seq"], "release": result["release"]}, ws["id"])
    return result


@app.post("/api/tools/{slug}/restart")
async def restart(slug: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_or_404(ws["id"], slug)
    _require_tool_admin(u, t)
    lock = _deploy_locks.setdefault(t["id"], asyncio.Lock())
    async with lock:
        try:
            await run_in_threadpool(deploy.restart, t, ws)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, str(e)[-3000:])
    db.audit(u["email"], "tool.restart", slug, None, ws["id"])
    return {"restarted": slug, "status": deploy.status(t)}


@app.get("/api/tools/{slug}/releases")
def releases(slug: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    with db.conn() as c:
        return [{k: v for k, v in dict(r).items() if k != "source"} | {"log": (r["log"] or "")[-1500:], "has_source": bool(r["source"])} for r in
                c.execute("SELECT seq, image, status, note, created, created_by, log, source FROM releases WHERE tool_id=? ORDER BY seq DESC LIMIT 20", (t["id"],))]


@app.get("/api/tools/{slug}/logs", response_class=PlainTextResponse)
def tool_logs(slug: str, request: Request, tail: int = 200, since: int | None = None, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    return deploy.logs(t, tail=min(tail, 5000), since=since)


@app.get("/api/tools/{slug}/source")
def tool_source(slug: str, request: Request, release: int | None = None, authorization: str | None = Header(None)):
    """The folder a release was built from, as a tar.gz. Admins only. This is
    what `bh pull` fetches: the document itself."""
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    with db.conn() as c:
        if release:
            r = c.execute("SELECT seq, source FROM releases WHERE tool_id=? AND seq=?", (t["id"], release)).fetchone()
        else:
            r = c.execute("SELECT seq, source FROM releases WHERE id=?", (t["current_release_id"],)).fetchone()
    if not r or not r["source"] or not __import__("pathlib").Path(r["source"]).exists():
        raise HTTPException(404, "no source is kept for that release (it predates hosted source); deploy once from a folder")
    db.audit(u["email"], "tool.pull", slug, {"release": r["seq"]}, u["workspace_id"])
    return FileResponse(r["source"], media_type="application/gzip", filename=f"{slug}-{r['seq']}.tar.gz",
                        headers={"X-Boathouse-Release": str(r["seq"])})


@app.get("/api/tools/{slug}/export")
def tool_export(slug: str, request: Request, authorization: str | None = Header(None)):
    """The whole tool as one tar.gz: source/, database.sql, data/, README.txt. Admins only.
    This is the copy the FAQ promises, available any day, not only if Boathouse winds down."""
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    ws = _ws(u)
    with db.conn() as c:
        r = c.execute("SELECT seq, source FROM releases WHERE id=?", (t["current_release_id"],)).fetchone() if t["current_release_id"] else None
    try:
        path = export.build(dict(t), dict(ws), r["source"] if r else None, r["seq"] if r else None)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    db.audit(u["email"], "tool.export", slug, {"bytes": path.stat().st_size, "release": r["seq"] if r else None}, u["workspace_id"])
    return FileResponse(path, media_type="application/gzip", filename=f"{slug}-export.tar.gz", background=BackgroundTask(path.unlink))


@app.get("/api/tools/{slug}/restore-points")
def tool_restore_points(slug: str, request: Request, authorization: str | None = Header(None)):
    """The nightly backups that hold this tool, newest first. Admins only."""
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    return restore.points(t)


@app.post("/api/tools/{slug}/restore")
async def tool_restore(slug: str, request: Request, body: dict, authorization: str | None = Header(None)):
    """Put the database and files back from a nightly backup. Without confirm=true this only says what
    would happen; with it, the current state is saved first, the tool is stopped, replaced, and started."""
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    pts = restore.points(t)
    if not pts:
        raise HTTPException(404, "no nightly backup holds this tool yet (the first one runs at 03:35 UTC)")
    date = body.get("date") or pts[0]["date"]
    pt = next((p for p in pts if p["date"] == date), None)
    if not pt:
        raise HTTPException(404, f"no backup of {slug} dated {date}; have: " + ", ".join(p["date"] for p in pts))
    parts = [x for x, ok in (("the database", pt["database"]), ("every file under /data", pt["files"])) if ok]
    plan = {"tool": slug, "date": date, "database": pt["database"], "files": pt["files"],
            "replaces": " and ".join(parts) or "nothing",
            "keeps": "a copy of what the tool holds right now is kept on the server first, in case this was a mistake",
            "downtime": "the tool stops for the length of the restore, usually seconds"}
    if not body.get("confirm"):
        return {"plan": plan, "confirm": False}
    ws = _ws(u)
    try:
        done = await run_in_threadpool(restore.restore, dict(t), dict(ws), date)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(500, str(e))
    db.audit(u["email"], "tool.restore", slug, done, u["workspace_id"])
    return {"plan": plan, "confirm": True, **done}


# ---- secrets ---------------------------------------------------------------------

@app.get("/api/tools/{slug}/secrets")
def list_secrets(slug: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT name, updated, updated_by FROM secrets WHERE tool_id=? ORDER BY name", (t["id"],))]


@app.put("/api/tools/{slug}/secrets/{name}")
async def set_secret(slug: str, name: str, request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_or_404(ws["id"], slug)
    _require_tool_admin(u, t)
    if not re.match(r"^[A-Z][A-Z0-9_]{0,63}$", name):
        raise HTTPException(422, "secret names are UPPER_SNAKE_CASE")
    if name in ("PORT", "DATABASE_URL", "DATA_DIR") or name.startswith("BOATHOUSE_"):
        raise HTTPException(422, f"{name} is set by Boathouse")
    with db.conn() as c:
        c.execute("""INSERT INTO secrets VALUES (?,?,?,?,?) ON CONFLICT(tool_id,name)
                     DO UPDATE SET value_enc=excluded.value_enc, updated=excluded.updated, updated_by=excluded.updated_by""",
                  (t["id"], name, auth.encrypt(str(body.get("value", ""))), time.time(), u["email"]))
    db.audit(u["email"], "secret.set", f"{slug}/{name}", None, ws["id"])
    restarted = False
    if deploy.status(t)["state"] == "running" and body.get("restart", True):
        lock = _deploy_locks.setdefault(t["id"], asyncio.Lock())
        async with lock:
            await run_in_threadpool(deploy.restart, t, ws)
        restarted = True
    return {"set": name, "restarted": restarted}


@app.delete("/api/tools/{slug}/secrets/{name}")
def delete_secret(slug: str, name: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    with db.conn() as c:
        c.execute("DELETE FROM secrets WHERE tool_id=? AND name=?", (t["id"], name))
    db.audit(u["email"], "secret.delete", f"{slug}/{name}", None, u["workspace_id"])
    return {"deleted": name, "note": "takes effect on next restart or deploy"}


# ---- people and sharing ------------------------------------------------------------

def _ensure_user(ws, email: str, role: str, by: str, name: str | None = None):
    """Add or update a person. Returns (user, invite_url or None)."""
    with db.conn() as c:
        c.execute("""INSERT INTO users (id, workspace_id, email, name, role, created, created_by) VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(workspace_id,email) DO UPDATE SET role=excluded.role, name=COALESCE(excluded.name, users.name)""",
                  (db.new_id("u"), ws["id"], email, name, role, time.time(), by))
    user = _user(ws["id"], email)
    invite = None
    if auth.needs_invite(user):
        invite = _invite_url(ws, auth.create_invite(ws["id"], email, by))
    return user, invite


@app.post("/api/tools/{slug}/grants")
def add_grant(slug: str, request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    ws = _ws(u)
    t = _tool_or_404(ws["id"], slug)
    _require_tool_admin(u, t)
    email = (body.get("email") or "").lower().strip()
    tier = (body.get("tier") or body.get("role") or "viewer").strip().lower()
    labels = body.get("labels") or []
    if isinstance(labels, str):
        labels = [x.strip() for x in labels.split(",") if x.strip()]
    if "@" not in email:
        raise HTTPException(422, "need an email")
    if tier not in TIERS:
        raise HTTPException(422, "tier must be viewer, editor or admin")
    if any(not LABEL_RE.match(x) for x in labels):
        raise HTTPException(422, "labels are short lowercase words")
    return _grant(ws, t, email, tier, labels, u["email"])


def _grant(ws, t, email: str, tier: str, labels: list, by: str) -> dict:
    """Let one person into one tool at one tier, tell them by email, and say what happened. Used by sharing and by allowed requests."""
    label_str = ",".join(sorted(set(labels)))
    existing = _user(ws["id"], email)
    # sharing with a new person makes them a guest of the workspace
    user, invite = _ensure_user(ws, email, existing["role"] if existing else "guest", by)
    with db.conn() as c:
        c.execute("""INSERT INTO grants (id, tool_id, email, role, created, created_by, tier, labels) VALUES (?,?,?,?,?,?,?,?)
                     ON CONFLICT(tool_id,email) DO UPDATE SET role=excluded.role, tier=excluded.tier, labels=excluded.labels,
                     created=excluded.created, created_by=excluded.created_by""",
                  (db.new_id("g"), t["id"], email, tier, time.time(), by, tier, label_str))
    db.audit(by, "grant.set", t["slug"], {"email": email, "tier": tier, "labels": label_str}, ws["id"])
    emailed = mail.share_notice(email, by, t["name"], t["slug"], hosts.tool_url(ws, t["slug"]), tier, invite, ws["slug"])
    return {"tool": t["slug"], "email": email, "tier": tier, "labels": label_str, "url": hosts.tool_url(ws, t["slug"]), "workspace": ws["slug"],
            "account_exists": auth.has_password(email), "emailed": emailed, "mail_configured": mail.configured(),
            "invite_url": invite, "invite_expires_days": config.INVITE_DAYS if invite else None}


# ---- asking to be let in: the Google Docs "request access" button, for agents --------

def _tier_of_email(ws, t, email: str) -> str | None:
    """The tier this email already has on the tool: owners of the workspace count as admin."""
    u = _user(ws["id"], email)
    if u and u["role"] == "owner":
        return "admin"
    with db.conn() as c:
        g = c.execute("SELECT tier FROM grants WHERE tool_id=? AND email=?", (t["id"], email)).fetchone()
    return g["tier"] if g else None


def _can_decide(email: str, ws, t) -> bool:
    return _tier_of_email(ws, t, email) == "admin"


def _deciders(ws, t) -> list[str]:
    with db.conn() as c:
        admins = [r["email"] for r in c.execute("SELECT email FROM grants WHERE tool_id=? AND tier='admin'", (t["id"],))]
    return sorted(set(_owners(ws["id"]) + admins))


def _request_or_404(rid: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM access_requests WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "no such request")
    return r


def _decide_request(r, decision: str, by: str) -> dict:
    """Allow or decline one pending request. Allowing is exactly a share by the decider."""
    if r["decided"]:
        raise HTTPException(410, f"already {r['decision']}")
    if r["expires"] < time.time():
        raise HTTPException(410, "this request expired; ask again")
    with db.conn() as c:
        ws = c.execute("SELECT * FROM workspaces WHERE id=?", (r["workspace_id"],)).fetchone()
        t = c.execute("SELECT * FROM tools WHERE id=?", (r["tool_id"],)).fetchone()
    if not ws or not t:
        raise HTTPException(410, "that tool is gone")
    with db.conn() as c:
        c.execute("UPDATE access_requests SET decided=?, decision=?, decided_by=? WHERE id=?", (time.time(), decision, by, r["id"]))
    db.audit(by, f"request.{decision}", t["slug"], {"email": r["email"], "tier": r["tier"]}, ws["id"])
    if decision != "allowed":
        return {"id": r["id"], "decision": decision, "email": r["email"], "tool": t["slug"]}
    return {"id": r["id"], "decision": "allowed", **_grant(ws, t, r["email"], r["tier"], [], by)}


@app.get("/api/referral")
def my_referral(request: Request, authorization: str | None = Header(None)):
    """This person's code, link, and what it has earned. Any signed-in person."""
    u = _actor(request, authorization)
    return referrals.summary(u["email"])


@app.get("/api/referral/payouts")
def referral_payouts(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    return {"payouts": referrals.payouts()}


@app.post("/api/referral/payouts/{email}")
def referral_mark_paid(email: str, request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    ref = body.get("ref")
    if not isinstance(ref,str) or not ref.strip():
        raise HTTPException(422, "say how it was paid: {\"ref\": \"PayPal 2026-10-01\"}")
    ref = ref.strip()
    cents = referrals.mark_paid(email, ref, u["email"], body.get('payout_id'), body.get('cents'))
    return {"email": email.lower(), "paid_cents": cents, "ref": ref}


@app.post('/api/referral/payouts')
def referral_prepare_payout(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    email = body.get('email')
    if not isinstance(email,str) or '@' not in email:
        raise HTTPException(422,'Supply the partner email.')
    return referrals.prepare_payout(email,u['email'])


@app.post("/api/access-requests")
def create_access_request(request: Request, body: dict, authorization: str | None = Header(None)):
    """Any signed-in person asks for a tier on any tool, by workspace/tool name. The tool's owners and admins get an
    email with one Allow button. Nobody on their side runs a command."""
    u = _actor(request, authorization)
    wslug = (body.get("workspace") or "").strip().lower()
    tslug = (body.get("tool") or "").strip().lower()
    tier = (body.get("tier") or "viewer").strip().lower()
    message = (body.get("message") or "").strip()[:280]
    if tier not in TIERS:
        raise HTTPException(422, "tier must be viewer, editor or admin")
    ws = db.workspace(wslug) if wslug else None
    t = _tool_by_slug(ws["id"], tslug) if ws and tslug else None
    if not ws or not t:
        raise HTTPException(404, f"no tool called {wslug}/{tslug}")
    have = _tier_of_email(ws, t, u["email"])
    if have and TIERS.index(have) >= TIERS.index(tier):
        return {"already": True, "tier": have, "tool": tslug, "workspace": wslug}
    if auth.throttle(f"request|{u['email']}", limit=20, window=3600):
        raise HTTPException(429, "too many requests in an hour")
    auth.record_attempt(f"request|{u['email']}")
    token = "req_" + secrets.token_urlsafe(24)
    now = time.time()
    rid = db.new_id("rq")
    with db.conn() as c:
        c.execute("DELETE FROM access_requests WHERE tool_id=? AND email=? AND decided IS NULL", (t["id"], u["email"]))
        c.execute("INSERT INTO access_requests VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                  (rid, ws["id"], t["id"], u["email"], tier, message, auth._token_hash(token), now, now + config.INVITE_DAYS * 86400))
    link = f"https://{config.PLATFORM_DOMAIN}/requests/{rid}?t={token}"
    to = [e for e in _deciders(ws, t) if e != u["email"]]
    sent = [e for e in to if mail.request_notice(e, u["email"], t["name"], t["slug"], tier, link, ws["name"], message)]
    db.audit(u["email"], "request.sent", t["slug"], {"tier": tier, "to": len(to)}, ws["id"])
    return {"id": rid, "tool": tslug, "workspace": wslug, "tier": tier, "asked_count": len(to), "emailed": bool(sent), "sent_to": len(sent),
            "mail_configured": mail.configured(), "expires_days": config.INVITE_DAYS}


@app.get("/api/access-requests")
def list_access_requests(request: Request, authorization: str | None = Header(None)):
    """Pending requests on tools this person may decide about, in the workspace the call is aimed at."""
    u = _actor(request, authorization)
    _require_member(u)
    ws = _ws(u)
    out = []
    with db.conn() as c:
        rows = c.execute("""SELECT r.*, t.slug AS tool_slug, t.name AS tool_name FROM access_requests r JOIN tools t ON t.id=r.tool_id
                            WHERE r.workspace_id=? AND r.decided IS NULL AND r.expires>? ORDER BY r.created""", (ws["id"], time.time())).fetchall()
    for r in rows:
        with db.conn() as c:
            t = c.execute("SELECT * FROM tools WHERE id=?", (r["tool_id"],)).fetchone()
        if _can_decide(u["email"], ws, t):
            out.append({"id": r["id"], "email": r["email"], "tool": r["tool_slug"], "name": r["tool_name"], "tier": r["tier"],
                        "message": r["message"], "created": r["created"]})
    return {"requests": out}


@app.post("/api/access-requests/{rid}/allow")
def allow_access_request(rid: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    r = _request_or_404(rid)
    with db.conn() as c:
        ws = c.execute("SELECT * FROM workspaces WHERE id=?", (r["workspace_id"],)).fetchone()
        t = c.execute("SELECT * FROM tools WHERE id=?", (r["tool_id"],)).fetchone()
    if not ws or not t or not _can_decide(u["email"], ws, t):
        raise HTTPException(403, "only an owner of the workspace or an admin on the tool can allow this")
    return _decide_request(r, "allowed", u["email"])


@app.delete("/api/tools/{slug}/grants/{email}")
def remove_grant(slug: str, email: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    t = _tool_or_404(u["workspace_id"], slug)
    _require_tool_admin(u, t)
    with db.conn() as c:
        c.execute("DELETE FROM grants WHERE tool_id=? AND email=?", (t["id"], email.lower()))
    db.audit(u["email"], "grant.remove", slug, {"email": email}, u["workspace_id"])
    return {"removed": email}


@app.get("/api/users")
def list_users(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_member(u)
    with db.conn() as c:
        rows = c.execute("SELECT email, name, role, created, created_by, last_login FROM users WHERE workspace_id=? ORDER BY role, email",
                         (u["workspace_id"],)).fetchall()
    # never the hash itself: a shared-in admin, an MCP client's log, or a model's context must not see it
    return [dict(r) | {"has_password": auth.has_password(r["email"])} for r in rows]


@app.post("/api/users")
def add_user(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_owner(u)
    ws = _ws(u)
    email = (body.get("email") or "").lower().strip()
    role = body.get("role", "member")
    if "@" not in email or role not in ("owner", "member", "guest"):
        raise HTTPException(422, "need an email and role owner|member|guest")
    user, invite = _ensure_user(ws, email, role, u["email"], body.get("name"))
    db.audit(u["email"], "user.set", email, {"role": role}, ws["id"])
    sign_in_url = None
    if invite:
        emailed = mail.invite_notice(email, u["email"], ws["name"], role, invite)
    else:
        sign_in_url, emailed = _existing_member_notice(ws, email, u["email"])
    return {"email": email, "role": role, "invite_url": invite, "invite_expires_days": config.INVITE_DAYS if invite else None,
            "sign_in_url": sign_in_url, "emailed": emailed, "mail_configured": mail.configured()}


@app.post("/api/users/{email}/invite")
def reinvite(email: str, request: Request, authorization: str | None = Header(None)):
    """Invite a new account, or send an existing account its normal sign-in page."""
    u = _actor(request, authorization)
    ws = _ws(u)
    email = email.lower()
    if email != u["email"]:
        _require_owner(u)
    if not _user(ws["id"], email):
        raise HTTPException(404, f"{email} is not in this workspace")
    if auth.has_password(email):
        url, emailed = _existing_member_notice(ws, email, u["email"])
        return {"email": email, "invite_url": None, "sign_in_url": url, "expires_days": None,
                "emailed": emailed, "mail_configured": mail.configured(),
                "note": "They can open the workspace with their usual Boat House password. Password recovery is available on the sign-in page."}
    token = auth.create_invite(ws["id"], email, u["email"])
    db.audit(u["email"], "invite.create", email, None, ws["id"])
    link = _invite_url(ws, token)
    target = _user(ws["id"], email)
    emailed = mail.invite_notice(email, u["email"], ws["name"], target["role"] if target else "member", link)
    return {"email": email, "invite_url": link, "expires_days": config.INVITE_DAYS, "emailed": emailed, "mail_configured": mail.configured()}


@app.delete("/api/users/{email}")
def remove_user(email: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_owner(u)
    email = email.lower()
    if email == u["email"]:
        raise HTTPException(422, "you cannot remove yourself")
    wid = u["workspace_id"]
    with db.conn() as c:
        c.execute("DELETE FROM users WHERE workspace_id=? AND email=?", (wid, email))
        c.execute("DELETE FROM sessions WHERE workspace_id=? AND email=?", (wid, email))
        c.execute("DELETE FROM project_keys WHERE workspace_id=? AND email=?", (wid, email))
        c.execute("DELETE FROM invites WHERE workspace_id=? AND email=?", (wid, email))
        c.execute("DELETE FROM grants WHERE email=? AND tool_id IN (SELECT id FROM tools WHERE workspace_id=?)", (email, wid))
    db.audit(u["email"], "user.remove", email, None, wid)
    return {"removed": email, "note": "sessions, keys, invites and grants revoked immediately"}


# ---- project keys ------------------------------------------------------------------

@app.post("/api/keys")
def create_key(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    name = (body.get("name") or "cli").strip()[:60]
    for_email = (body.get("for") or u["email"]).lower()
    if for_email != u["email"]:
        _require_owner(u)
        target = _user(u["workspace_id"], for_email)
        if not target:
            raise HTTPException(422, f"{for_email} is not in this workspace; bh users add {for_email} --role member first")
        if target["role"] not in ("owner", "member"):
            with db.conn() as c:
                admin_somewhere = c.execute("SELECT 1 FROM grants g JOIN tools t ON t.id=g.tool_id WHERE t.workspace_id=? AND g.email=? AND g.tier='admin'",
                                            (u["workspace_id"], for_email)).fetchone()
            if not admin_somewhere:
                raise HTTPException(422, f"keys go to owners, members, and people who are admin on a tool; {for_email} is a guest with no admin share. "
                                         f"Either bh share <tool> {for_email} --tier admin, or bh users add {for_email} --role member")
    key = auth.create_project_key(u["workspace_id"], for_email, name)
    db.audit(u["email"], "key.create", for_email, {"name": name}, u["workspace_id"])
    return {"key": key, "email": for_email, "name": name, "note": "shown once; store it in a keychain"}


@app.get("/api/keys")
def list_keys(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_member(u)
    with db.conn() as c:
        q = """SELECT id, email, name, created, last_used, (workspace_id='*') AS account FROM project_keys
               WHERE (workspace_id=? OR (workspace_id='*' AND email IN (SELECT email FROM users WHERE workspace_id=?)))"""
        args = [u["workspace_id"], u["workspace_id"]]
        if u["role"] != "owner":
            q += " AND email=?"
            args.append(u["email"])
        return [dict(r) for r in c.execute(q + " ORDER BY created DESC", args)]


@app.delete("/api/keys/{key_id}")
def revoke_key(key_id: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    with db.conn() as c:
        k = c.execute("""SELECT * FROM project_keys WHERE id=? AND (workspace_id=? OR (workspace_id=? AND email IN (SELECT email FROM users WHERE workspace_id=?)))""",
                      (key_id, u["workspace_id"], auth.ACCOUNT, u["workspace_id"])).fetchone()
        if not k:
            raise HTTPException(404)
        if k["email"] != u["email"]:
            _require_owner(u)
            if k["workspace_id"] == auth.ACCOUNT:
                raise HTTPException(403, f"that key is {k['email']}'s own and works in their other workspaces too; only they can revoke it. Remove them from this workspace instead: bh users rm {k['email']}")
        c.execute("DELETE FROM project_keys WHERE id=?", (key_id,))
    db.audit(u["email"], "key.revoke", key_id, None, u["workspace_id"])
    return {"revoked": key_id}


# ---- registrar (the host's account; owners only) -------------------------------------

def _reg(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except registrar.RegistrarError as e:
        raise HTTPException(e.status, {"error": str(e), "code": e.code})


def _is_host_owner(u) -> bool:
    return u["role"] == "owner" and _ws(u)["slug"] == config.HOST_WORKSPACE


def _require_host_owner(u):
    _require_owner(u)
    if _ws(u)["slug"] != config.HOST_WORKSPACE:
        raise HTTPException(403, "host workspace owners only")


@app.get("/api/registrar")
def registrar_status(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    return registrar.status()


@app.post("/api/registrar/connect")
def registrar_connect(request: Request, body: dict | None = None, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    if (body or {}).get("sandbox"):
        r = _reg(registrar.sandbox_keys, u["email"])
    else:
        r = _reg(registrar.connect_start, f"Boathouse ({config.PLATFORM_DOMAIN})", u["email"])
    db.audit(u["email"], "registrar.connect", None, {"sandbox": bool((body or {}).get("sandbox"))}, u["workspace_id"])
    return r


@app.post("/api/registrar/connect/finish")
def registrar_finish(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    r = _reg(registrar.connect_finish, u["email"])
    if r.get("state") == "connected":
        db.audit(u["email"], "registrar.connected", None, r, u["workspace_id"])
    return r


@app.put("/api/registrar/keys")
def registrar_keys(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    if not body.get("apikey") or not body.get("secret"):
        raise HTTPException(422, "need apikey and secret")
    registrar.set_keys(body["apikey"], body["secret"], u["email"])
    db.audit(u["email"], "registrar.keys_set", None, None, u["workspace_id"])
    return registrar.status()


# ---- domains (a workspace's addresses) ------------------------------------------------

def _domain_row(d) -> dict:
    return {"domain": d["domain"], "registrar": d["registrar"], "status": d["status"], "primary": bool(d["is_primary"]), "tool": (d["tool_slug"] if "tool_slug" in d.keys() else None),
            "dns_ok": bool(d["dns_ok"]), "cost_cents": d["cost_cents"], "created": d["created"], "created_by": d["created_by"]}


def _valid_domain(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if not DOMAIN_RE.match(d) or d == config.PLATFORM_DOMAIN or d.endswith("." + config.PLATFORM_DOMAIN):
        raise HTTPException(422, "that is not a domain Boathouse can hold for you")
    return d


@app.get("/api/domains")
def list_domains(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_member(u)
    ws = _ws(u)
    return {"free": f"{ws['slug']}.{config.PLATFORM_DOMAIN}", "primary": hosts.base_for(ws),
            "domains": [_domain_row(d) for d in hosts.workspace_domains(ws["id"])]}


@app.post("/api/domains/check")
def check_domain(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_member(u)
    d = _valid_domain(body.get("domain"))
    q = _reg(registrar.check, d)
    if isinstance(q, dict) and q.get("available"):
        m = billing.PRICES["domain_margin"]
        q["margin_cents"] = m
        q["registrar_cents"] = q["price_cents"]                     # the same figure under its honest name
        q["total_cents"] = q["price_cents"] + m                     # what the workspace actually pays now
        q["renewal_total_cents"] = (q.get("renewal_cents") or q.get("regular_cents") or q["price_cents"]) + m
    return q


def _insert_domain(ws, d: str, reg: str, by: str, cost: int | None, order_id: str | None):
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        domain_payments.require_namespace(c, ws["id"], d)
        if c.execute("SELECT 1 FROM domains WHERE domain=?", (d,)).fetchone():
            raise HTTPException(409, "This domain is already attached.")
        owner = c.execute("SELECT workspace_id FROM domain_allocations WHERE domain=?", (d,)).fetchone()
        if owner and owner["workspace_id"] != ws["id"]:
            raise HTTPException(409, "This domain belongs to another workspace.")
        allocated = c.execute("SELECT workspace_id,state FROM domain_operations WHERE domain=? AND state IN ('processing','uncertain','purchased')", (d,)).fetchone()
        if allocated and (allocated["workspace_id"] != ws["id"] or allocated["state"] != "purchased"):
            raise HTTPException(409, "This domain is already allocated or has a purchase in progress.")
        first = c.execute("SELECT COUNT(*) FROM domains WHERE workspace_id=? AND status='active'", (ws["id"],)).fetchone()[0] == 0
        c.execute("""INSERT INTO domains (id, workspace_id, domain, registrar, status, is_primary, dns_ok, cost_cents, order_id, created, created_by)
                     VALUES (?,?,?,?,'active',?,0,?,?,?,?)""",
                  (db.new_id("d"), ws["id"], d, reg, 1 if first else 0, cost, order_id, time.time(), by))
        if reg == "porkbun":
            c.execute("INSERT OR IGNORE INTO domain_allocations (domain,workspace_id,created) VALUES (?,?,?)", (d, ws["id"], time.time()))
        c.execute("COMMIT")


def _point_dns(d: str) -> dict:
    if not config.PUBLIC_IP:
        raise HTTPException(500, "BH_PUBLIC_IP is not set on the box")
    recs = _reg(registrar.point, d, config.PUBLIC_IP)
    with db.conn() as c:
        c.execute("UPDATE domains SET dns_ok=1 WHERE domain=?", (d,))
    return {"records": recs}


@app.post("/api/domains")
def buy_domain(request: Request, body: dict, authorization: str | None = Header(None)):
    """Quote first; reserve credit and confirm exactly that persisted operation."""
    u = _actor(request, authorization)
    _require_owner(u)
    ws = _ws(u)
    d = _valid_domain(body.get("domain"))
    if type(body.get("confirm", False)) is not bool:
        raise HTTPException(422, "confirm must be true or false")
    if not body.get("confirm", False):
        return _reg(domain_payments.quote, ws, d, u["email"])
    operation = _reg(domain_payments.purchase, ws, d, body.get("quote_id"), body.get("max_cost_cents"))
    result = json.loads(operation["provider_result"] or "{}")
    dns = []
    dns_ok = False
    with db.conn() as c:
        attached = c.execute("SELECT * FROM domains WHERE domain=? AND workspace_id=?", (d, ws["id"])).fetchone()
    note = "DNS takes a few minutes to spread; certificates are issued on first visit."
    if attached:
        try:
            dns = _point_dns(d)["records"]
            dns_ok = True
        except Exception:
            # Registration and payment already succeeded. This is repairable DNS
            # work, never a purchase failure or a reason to buy the domain again.
            note = "Your domain is purchased. DNS setup is pending; retry this same purchase quote or use domain_repoint to finish setup without another charge."
    else:
        note = "This domain was purchased earlier and is currently detached. Attach it to serve it again; there is no new charge."
    return {"dry_run": False, "bought": d, "quote_id": operation["id"], "operation_id": operation["id"],
            "state": "purchased", "cost_cents": operation["total_cents"], "order_id": result.get("orderId"),
            "balance_cents": billing.balance(ws["id"]), "primary": hosts.base_for(ws) == d,
            "dns": dns, "dns_ok": dns_ok, "dns_pending": bool(attached) and not dns_ok,
            "urls": {"home": f"https://{d}/", "auth": f"https://auth.{d}/", "tools": f"https://<tool>.{d}/"}, "note": note}


@app.post("/api/domains/{domain}/attach")
def attach_domain(domain: str, request: Request, body: dict | None = None, authorization: str | None = Header(None)):
    """Attach a domain the host's registrar account already holds (DNS is set for
    you) or one held elsewhere (you are told which records to make)."""
    u = _actor(request, authorization)
    _require_owner(u)
    ws = _ws(u)
    d = _valid_domain(domain)
    with db.conn() as c:
        if c.execute("SELECT 1 FROM domains WHERE domain=?", (d,)).fetchone():
            raise HTTPException(409, f"{d} is already attached")
    held = registrar.creds() is not None and _reg(registrar.owned, d)
    with db.conn() as c:
        purchase = c.execute("SELECT workspace_id,state FROM domain_operations WHERE domain=? AND state IN ('processing','uncertain','purchased')", (d,)).fetchone()
        owner = c.execute("SELECT workspace_id FROM domain_allocations WHERE domain=?", (d,)).fetchone()
    if owner and owner["workspace_id"] != ws["id"]:
        raise HTTPException(409, "This domain belongs to another workspace.")
    if purchase and (purchase["workspace_id"] != ws["id"] or purchase["state"] != "purchased"):
        raise HTTPException(409, "This domain is already allocated or has a purchase in progress.")
    if held and not purchase and not owner and not _is_host_owner(u):
        raise HTTPException(403, "The host must assign this registrar-managed domain to your workspace before it can be attached.")
    _insert_domain(ws, d, "porkbun" if held else "external", u["email"], None, None)
    db.audit(u["email"], "domain.attach", d, {"held": held}, ws["id"])
    if held:
        try:
            dns = _point_dns(d)
            return {"attached": d, "registrar": "porkbun", "dns": dns["records"], "dns_ok": True,
                    "dns_pending": False, "primary": hosts.base_for(ws) == d}
        except Exception:
            return {"attached": d, "registrar": "porkbun", "dns": [], "dns_ok": False,
                    "dns_pending": True, "primary": hosts.base_for(ws) == d,
                    "note": "The domain is attached. DNS setup is pending; use domain_repoint to finish setup."}
    return {"attached": d, "registrar": "external", "primary": hosts.base_for(ws) == d,
            "make_these_records": [{"name": "@", "type": "A", "content": config.PUBLIC_IP}, {"name": "*", "type": "A", "content": config.PUBLIC_IP}]}


@app.post("/api/domains/{domain}/dns")
def repoint_domain(domain: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_owner(u)
    d = _valid_domain(domain)
    with db.conn() as c:
        row = c.execute("SELECT * FROM domains WHERE domain=? AND workspace_id=?", (d, u["workspace_id"])).fetchone()
    if not row or row["registrar"] != "porkbun":
        raise HTTPException(404, "not a domain this Boathouse holds")
    return {"domain": d, **_point_dns(d)}


@app.get("/api/domains/{domain}/dns")
def domain_records(domain: str, request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_owner(u)
    d = _valid_domain(domain)
    with db.conn() as c:
        row = c.execute("SELECT registrar FROM domains WHERE domain=? AND workspace_id=?", (d, u["workspace_id"])).fetchone()
    if not row or row["registrar"] != "porkbun":
        raise HTTPException(404, "This workspace does not manage that domain's DNS.")
    return {"domain": d, "records": _reg(registrar.records, d)}


@app.patch("/api/domains/{domain}")
def patch_domain(domain: str, request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_owner(u)
    d = _valid_domain(domain)
    with db.conn() as c:
        if not c.execute("SELECT 1 FROM domains WHERE domain=? AND workspace_id=?", (d, u["workspace_id"])).fetchone():
            raise HTTPException(404, "that domain is not attached to this workspace")
    if body.get("primary"):
        with db.conn() as c:
            c.execute("UPDATE domains SET is_primary=0 WHERE workspace_id=?", (u["workspace_id"],))
            c.execute("UPDATE domains SET is_primary=1 WHERE domain=?", (d,))
        db.audit(u["email"], "domain.primary", d, None, u["workspace_id"])
    if "tool" in body:
        slug = (body.get("tool") or "").lower()
        if slug and not _tool_by_slug(u["workspace_id"], slug):
            raise HTTPException(404, f"no tool named {slug}")
        with db.conn() as c:
            c.execute("UPDATE domains SET tool_slug=? WHERE domain=?", (slug or None, d))
        db.audit(u["email"], "domain.point", d, {"tool": slug or None}, u["workspace_id"])
    return list_domains(request, authorization)


@app.delete("/api/domains/{domain}")
def detach_domain(domain: str, request: Request, authorization: str | None = Header(None)):
    """Stops serving the domain. The registration itself stays in the registrar account."""
    u = _actor(request, authorization)
    _require_owner(u)
    d = _valid_domain(domain)
    with db.conn() as c:
        c.execute("DELETE FROM domains WHERE domain=? AND workspace_id=?", (d, u["workspace_id"]))
    db.audit(u["email"], "domain.detach", d, None, u["workspace_id"])
    return {"detached": d, "note": "the registration is untouched; bh domain attach brings it back"}


@app.post("/api/domains/free-address")
def free_address(request: Request, authorization: str | None = Header(None)):
    """Make <ws>.<platform> and *.<ws>.<platform> resolve to this box. Needs the
    platform domain in the host's registrar account."""
    u = _actor(request, authorization)
    _require_owner(u)
    ws = _ws(u)
    if not config.PUBLIC_IP:
        raise HTTPException(500, "BH_PUBLIC_IP is not set on the box")
    recs = _reg(registrar.point, config.PLATFORM_DOMAIN, config.PUBLIC_IP, (ws["slug"], f"*.{ws['slug']}"))
    db.audit(u["email"], "domain.free_address", f"{ws['slug']}.{config.PLATFORM_DOMAIN}", None, ws["id"])
    return {"address": f"{ws['slug']}.{config.PLATFORM_DOMAIN}", "records": recs}


@app.post("/api/platform/dns")
def platform_dns(request: Request, authorization: str | None = Header(None)):
    """Point the platform domain itself (apex, api., mcp., and the wildcard) at this box."""
    u = _actor(request, authorization)
    _require_host_owner(u)
    if not config.PUBLIC_IP:
        raise HTTPException(500, "BH_PUBLIC_IP is not set on the box")
    recs = _reg(registrar.point, config.PLATFORM_DOMAIN, config.PUBLIC_IP, ("", "api", "mcp", "*"))
    db.audit(u["email"], "platform.dns", config.PLATFORM_DOMAIN, None, u["workspace_id"])
    return {"platform": config.PLATFORM_DOMAIN, "records": recs}


# ---- signup: a new workspace from nothing but an email --------------------------------

def validate_workspace(name: str, email: str, code: str | None = None) -> tuple[str, str | None]:
    email = (email or "").strip().lower()
    referrer = None
    if (code or "").strip():
        referrer = referrals.lookup(code)
        if not referrer:
            raise HTTPException(422, "that referral code is not one of ours; check it, or leave it out")
        if referrer == email:
            raise HTTPException(422, "that is your own code; it works for other people")
    slug = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")[:24]
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(422, "a real email address is needed")
    if len(slug) < 3 or slug in RESERVED or slug in ("api", "mcp", "auth", "www", "admin", "billing", "account"):
        raise HTTPException(422, "workspace name must give at least 3 letters or digits and not be a reserved word")
    if db.workspace(slug):
        raise HTTPException(409, f"the name {slug} is taken; pick another")
    return slug, referrer


def create_workspace(name: str, email: str, by: str | None = None, password: str | None = None, code: str | None = None,
                     signup_proof: str | None = None) -> dict:
    """Create a workspace. Public signup passes a mailbox proof; host-created owners remain invited."""
    email = (email or "").strip().lower()
    slug, referrer = validate_workspace(name, email, code)
    proof = auth.signup_details(signup_proof) if signup_proof else None
    if signup_proof and (not proof or proof["email"] != email or proof["workspace"] != name or proof["code"] != (code or "")):
        raise HTTPException(410, "This confirmation link expired or was already used. Please sign up again.")
    now = time.time()
    pw_hash = auth.hash_password(password) if password else None
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        # Signup may have checked before another invitation/signup initialized
        # this email. Never replace an account password using that stale result.
        if pw_hash and auth._has_password(c, email):
            raise HTTPException(409, "That email already has a Boat House password. Sign in and create the workspace from your account page.")
        if c.execute("SELECT 1 FROM workspaces WHERE slug=?", (slug,)).fetchone():
            raise HTTPException(409, f"the name {slug} is taken; pick another")
        if signup_proof:
            spent = c.execute("INSERT OR IGNORE INTO used_tokens (hash, expires) VALUES (?,?)", (auth._token_hash(signup_proof), proof["exp"]))
            if spent.rowcount != 1:
                raise HTTPException(410, "This confirmation link was already used. Sign in to your account.")
        try:
            referral = referrals.attribution(c,email,code,now)
        except referrals.ReferralError as e:
            raise HTTPException(422,str(e))
        referrer = referral['referrer_email'] if referral else None
        discount_until = referral['discount_until'] if referral else None
        wid = db.new_id("ws")
        c.execute("INSERT INTO workspaces (id, slug, name, created, owner_email, referred_by, discount_until) VALUES (?,?,?,?,?,?,?)", (wid, slug, name.strip()[:80] or slug, now, email,referrer,discount_until))
        c.execute("INSERT INTO users (id, workspace_id, email, name, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                  (db.new_id("u"), wid, email, None, "owner", now, by or "signup"))
        if pw_hash:
            auth._write_password(c, email, pw_hash, None)
        c.execute("COMMIT")
    ws = db.workspace(slug)
    token = None
    if not auth.has_password(email):
        token = auth.create_invite(wid, email, by or "signup")
    if config.TRIAL_CREDIT_CENTS:
        billing.post(wid, "grant", config.TRIAL_CREDIT_CENTS, "welcome credit", f"welcome:{wid}", "signup")
    if referrer:
        db.audit(by or email, "referral.used", slug, {"code": referral['code'], "referrer": referrer}, wid)
    dns = None
    if registrar.creds() and config.PUBLIC_IP:
        try:
            dns = registrar.point(config.PLATFORM_DOMAIN, config.PUBLIC_IP, (slug, f"*.{slug}"))
        except registrar.RegistrarError as e:
            dns = {"error": str(e)}
    db.audit(by or email, "workspace.create", slug, {"email": email, "dns": bool(dns and not isinstance(dns, dict))}, wid)
    return {"workspace": slug, "name": ws["name"], "home": f"https://{slug}.{config.PLATFORM_DOMAIN}/",
            "api": f"https://{config.API_HOST}", "mcp": f"https://{config.MCP_HOST}/mcp",
            "invite_url": _invite_url(ws, token) if token else None, "invite_expires_days": config.INVITE_DAYS if token else None,
            "account": f"https://{config.PLATFORM_DOMAIN}/account?ws={slug}",
            "welcome_credit_cents": config.TRIAL_CREDIT_CENTS, "dns": dns,
            "referred_by": referrer, "discount_until": discount_until}


@app.post("/api/claim")
def api_claim(request: Request, body: dict):
    """Public, throttled. Trades a one-time claim code (from the welcome page or a new-key page) for the project key
    it stands for. The code is dead afterwards, so a code that leaked into a chat or a shell history is worthless."""
    w = _where(request)
    if w.kind not in ("platform", "api"):
        raise HTTPException(404)
    ip = f"claim|{_ip(request)}"
    if auth.throttle(ip, limit=12, window=900):
        raise HTTPException(429, "too many attempts from this address; wait fifteen minutes")
    auth.record_attempt(ip)
    got = auth.redeem_claim(body.get("code", ""))
    if not got:
        raise HTTPException(410, "that code is unknown, already used, or older than fifteen minutes; mint a new key on your account page")
    ws = db.workspace_by_id(got["workspace_id"])
    db.audit(got["email"], "key.claim", got["email"], {"ip": _ip(request)}, ws["id"])
    return {"key": got["key"], "api": f"https://{config.API_HOST}", "mcp": f"https://mcp.{config.PLATFORM_DOMAIN}/mcp",
            "workspace": ws["slug"], "email": got["email"]}


@app.get("/api/mail")
def mail_status(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    st = mail.settings()
    if not _is_host_owner(u):            # any signed-in person may ask whether share emails go out; the settings stay with the host
        return {"configured": bool(st)}
    return {"configured": bool(st), "provider": st["provider"] if st else None, "host": st["host"] if st else None,
            "user": st["user"] if st else None, "from": st["from"] if st else None}


@app.put("/api/mail")
def mail_set(request: Request, body: dict, authorization: str | None = Header(None)):
    """Host owners only: the mailbox Boat House sends from. The password never comes back out."""
    u = _actor(request, authorization)
    _require_host_owner(u)
    if body.get("provider") == "resend":
        if not body.get("api_key") or "@" not in (body.get("from") or ""):
            raise HTTPException(422, "need api_key and a from address")
        mail.set_resend(body["api_key"], body["from"], u["email"])
        db.audit(u["email"], "mail.configured", None, {"provider": "resend", "from": body["from"]})
        return {"configured": True, "provider": "resend", "from": body["from"]}
    for f in ("host", "user", "password"):
        if not body.get(f):
            raise HTTPException(422, f"need {f}")
    mail.set_smtp(body["host"], int(body.get("port") or 587), body["user"], body["password"], body.get("from") or body["user"], u["email"])
    db.audit(u["email"], "mail.configured", None, {"provider": "smtp", "host": body["host"], "user": body["user"]})
    return {"configured": True, "provider": "smtp", "host": body["host"], "user": body["user"]}


@app.post("/api/mail/test")
def mail_test(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    if not mail.configured():
        raise HTTPException(409, "email is not set up yet (bh mail set)")
    try:
        mail.test_notice(u["email"])
    except Exception as e:  # noqa: BLE001
        why = str(e)
        if (mail.settings() or {}).get("provider") != "resend" and ("unreachable" in why.lower() or "timed out" in why.lower()):
            why += " (this host cannot open SMTP ports; most clouds block them. Send over HTTPS instead: deploy/set-mail.sh --resend you@yourdomain)"
        raise HTTPException(502, f"the message did not go out: {why}")
    return {"sent_to": u["email"]}


def _mail_domain(u) -> dict:
    st = mail.settings()
    if not st or st["provider"] != "resend":
        raise HTTPException(409, "set the HTTPS provider first: bh mail set --resend --from you@yourdomain")
    domain = mail.sending_domain()
    if not registrar.creds():
        raise HTTPException(409, {"error": "no registrar connected, so the DNS records must be made by hand", "code": "NO_REGISTRAR"})
    try:
        did, status, wanted = mail.resend_domain_records(domain)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"domain": domain, "id": did, "status": status, "wanted": wanted}


@app.post("/api/mail/domain")
def mail_domain_setup(request: Request, authorization: str | None = Header(None)):
    """Host owners: make the from-address's domain a verified sender at Resend by writing the DNS records it asks
    for at the registrar, then asking Resend to check. Verification can lag DNS by minutes; GET polls it."""
    u = _actor(request, authorization)
    _require_host_owner(u)
    d = _mail_domain(u)
    written = []
    for rec in d["wanted"]:
        if rec["type"] not in ("TXT", "MX", "CNAME"):
            continue
        written.append(_reg(registrar.replace_record, d["domain"], rec["name"], rec["type"], rec["content"], rec.get("prio")))
    try:
        status = mail.resend_domain_status(d["id"])
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    mail._put({"domain_id": d["id"]}, u["email"])
    db.audit(u["email"], "mail.domain", d["domain"], {"records": len(written), "status": status})
    return {"domain": d["domain"], "status": status, "records": written}


@app.get("/api/mail/domain")
def mail_domain_status(request: Request, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    d = _mail_domain(u)
    try:
        status = mail.resend_domain_status(d["id"])
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"domain": d["domain"], "status": status}


@app.post("/api/signup")
def api_signup(request: Request, body: dict):
    """Create an invited workspace and email its owner a mailbox-verifying setup link.

    The shareable invitation returned here cannot authenticate the owner.
    """
    w = _where(request)
    if w.kind not in ("platform", "api"):
        raise HTTPException(404)
    key = f"signup|{_ip(request)}"
    if auth.throttle(key, limit=5, window=3600):
        raise HTTPException(429, "too many signups from this address; try again in an hour")
    auth.record_attempt(key)
    email = (body.get("email") or "").strip().lower()
    code = body.get('code')
    if code:
        if not isinstance(code,str):
            raise HTTPException(422,'Referral code must be text.')
        name = body.get('workspace') or body.get('name') or ''
        validate_workspace(name,email,code)
        # An anonymous agent cannot bind somebody else's email to its partner.
        # The owner selects and confirms the account through the normal flow.
        url = f'https://{config.PLATFORM_DOMAIN}/signup?'+urlencode({'workspace':name,'code':code.strip().upper()})
        return {'requires_signup':True,'signup_url':url,
                'note':'Open this signup link and confirm your email to apply the referral code. No account has been created yet.'}
    email_limit = f"signup-email|{email}"
    if auth.throttle(email_limit, limit=3):
        raise HTTPException(429, "please wait fifteen minutes before requesting another setup email")
    auth.record_attempt(email_limit)
    made = create_workspace(body.get("workspace") or body.get("name") or "", email, code=body.get("code"))
    if made["invite_url"]:
        emailed = mail.invite_notice(email, "Boat House", made["name"], "owner", made["invite_url"])
        sign_in_url = None
    else:
        sign_in_url, emailed = _existing_member_notice(db.workspace(made["workspace"]), email, "Boat House")
    return {**made, "email": email, "emailed": emailed, "mail_configured": mail.configured(), "sign_in_url": sign_in_url,
            "note": "Open the secure setup link in your email to finish. The shareable invitation can request another email." if made["invite_url"] else
                    "Open your workspace with your usual Boat House password."}


# ---- billing ---------------------------------------------------------------------------

def _bill(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except billing.InsufficientFunds as e:
        raise HTTPException(402, {"error": str(e), "code": "INSUFFICIENT_FUNDS"})
    except billing.StripeError as e:
        raise HTTPException(422, {"error": str(e), "code": "CARD"})


@app.get("/api/prices")
def prices(request: Request, authorization: str | None = Header(None)):
    """The price sheet; signed in, it also says this workspace's own rate when a referral discount is running."""
    sheet = billing.price_sheet()
    try:
        u = _actor(request, authorization)
    except HTTPException:
        return sheet
    ws = _ws(u)
    day = billing.daily_rate(ws)
    if billing.monthly_rate(ws) < 1000:
        sheet["your_tool_day"] = day
        sheet["words"].append(f"this workspace: half price, ${day/100:.2f} a day per tool, until "
                              f"{time.strftime('%Y-%m-%d', time.gmtime(ws['discount_until']))} (referral)")
    return sheet


@app.get("/api/billing")
def billing_status(request: Request, limit: int = 30, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_member(u)
    ws = _ws(u)
    with db.conn() as c:
        running = sum(1 for t in c.execute("SELECT * FROM tools WHERE workspace_id=?", (ws["id"],)) if deploy.status(t)["state"] == "running")
    day = billing.daily_rate(ws)       # half price while a referral discount lasts
    return {"workspace": ws["slug"], "balance_cents": billing.balance(ws["id"]), "paused": bool(ws["paused"]),
            "card_on_file": bool(ws["stripe_pm"]), "autorefill_cents": ws["autorefill_cents"], "autorefill_cap_cents": ws["autorefill_cap_cents"],
            "running_tools": running, "burn_cents_per_day": running * day, "tool_day_cents": day, "tool_month_cents": billing.monthly_rate(ws),
            "days_left": (billing.balance(ws["id"]) // (running * day)) if running else None,
            "ledger": billing.ledger(ws["id"], limit)}


@app.post("/api/billing/card")
def billing_card(request: Request, authorization: str | None = Header(None)):
    """A one-time link where the owner types the card. Nothing else about billing needs a browser."""
    u = _actor(request, authorization)
    _require_owner(u)
    ws = _ws(u)
    url = _bill(billing.card_link, ws, f"https://{config.PLATFORM_DOMAIN}/account?ws={ws['slug']}", u["email"])
    db.audit(u["email"], "billing.card_link", None, None, ws["id"])
    return {"url": url, "note": "open once, enter the card, done; then bh billing topup or bh billing autorefill"}


@app.post("/api/billing/topup")
def billing_topup(request: Request, body: dict, authorization: str | None = Header(None)):
    """Quote unless confirm=true. With confirm, charge the card on file and add credit."""
    u = _actor(request, authorization)
    _require_owner(u)
    ws = _ws(u)
    cents = body.get("cents")
    if type(cents) is not int or cents < 500 or cents > 100000:
        raise HTTPException(422, "top up between $5.00 and $1,000.00")
    confirm = body.get("confirm", False)
    if type(confirm) is not bool:
        raise HTTPException(422, "confirm must be true or false")
    if not confirm:
        quote = _bill(billing.quote_topup, ws, cents, u["email"], body.get("operation_id"))
        return {"dry_run": True, **quote, "cents": cents, "card_on_file": bool(ws["stripe_pm"]), "balance_after_cents": billing.balance(ws["id"]) + (0 if quote["payment_status"] == "succeeded" else cents),
                "message": ("This payment already succeeded; it will not charge again." if quote['payment_status']=='succeeded'
                            else f"Add ${cents/100:.2f} of hosting credit through secure Stripe checkout. Confirm to get the payment link; no card is charged until checkout is completed.")}

    result = _bill(checkout.start, ws, cents, u['email'], body.get('operation_id'))
    db.audit(u['email'], 'billing.checkout', None, {'cents':cents},ws['id'])
    return {'dry_run':False,'cents':cents,**result}


@app.post("/api/billing/autorefill")
def billing_autorefill(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_owner(u)
    ws = _ws(u)
    cents = body.get("cents", 0)
    cap = body.get("cap_cents", cents)
    changed = _bill(billing.set_auto_refill, ws, cents, cap)
    cap = changed["autorefill_cap_cents"]
    db.audit(u["email"], "billing.autorefill", None, {"cents": cents, "cap_cents": cap}, ws["id"])
    return {"autorefill_cents": cents, "autorefill_cap_cents": cap,
            "note": (f"when the balance drops under ${billing.REFILL_BELOW/100:.2f} the card is charged ${cents/100:.2f}" + (f", at most ${cap/100:.2f} a month" if cap else "")) if cents else "auto-refill off"}


@app.post("/api/billing/grant")
def billing_grant(request: Request, body: dict, authorization: str | None = Header(None)):
    """Host only: credit a workspace by hand (a wire, a gift, a correction)."""
    u = _actor(request, authorization)
    _require_host_owner(u)
    target = db.workspace((body.get("workspace") or "").lower())
    if not target:
        raise HTTPException(404, "no such workspace")
    cents = int(body.get("cents") or 0)
    if not cents:
        raise HTTPException(422, "cents required (negative to debit)")
    bal = billing.post(target["id"], "grant" if cents > 0 else "charge", cents, body.get("memo") or f"granted by {u['email']}", body.get("ref"), u["email"])
    if target["paused"] and bal > 0:
        billing.resume(target)
    db.audit(u["email"], "billing.grant", target["slug"], {"cents": cents, "memo": body.get("memo")}, target["id"])
    return {"workspace": target["slug"], "balance_cents": bal}


@app.put("/api/billing/stripe-keys")
def billing_stripe_keys(request: Request, body: dict, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_host_owner(u)
    if not body.get("secret") or not body.get("webhook_secret"):
        raise HTTPException(422, "need secret and webhook_secret")
    billing.set_stripe_keys(body["secret"], body["webhook_secret"], u["email"])
    db.audit(u["email"], "billing.stripe_keys_set", None, None, u["workspace_id"])
    return {"stripe": "connected", "webhook_url": f"https://{config.API_HOST}/stripe/webhook"}


@app.post("/api/billing/meter")
def billing_meter_now(request: Request, authorization: str | None = Header(None)):
    """Host only: run today's metering now instead of waiting for the hourly pass."""
    u = _actor(request, authorization)
    _require_host_owner(u)
    return {"lines": billing.meter_once()}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    w = _where(request)
    if w.kind != "api":
        raise HTTPException(404)
    payload = await request.body()
    try:
        return billing.handle_webhook(payload, request.headers.get("stripe-signature", ""))
    except billing.StripeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/audit")
def audit_log(request: Request, limit: int = 100, authorization: str | None = Header(None)):
    u = _actor(request, authorization)
    _require_owner(u)
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit WHERE workspace_id=? ORDER BY ts DESC LIMIT ?", (u["workspace_id"], min(limit, 1000)))]


@app.get("/api/health")
def health():
    return {"ok": True, "platform": config.PLATFORM_DOMAIN, "time": time.time()}


# Phase C modules: the MCP face (mcp.py) and the human front door (front.py).
# Both are routers so they can be built and tested apart from this file.
from . import front, mcp  # noqa: E402
app.include_router(front.router)
from . import partners_front
app.include_router(partners_front.router)
app.include_router(mcp.router)
