"""The human front door, all of it on the platform host (boathousecloud.com).

  /            what this is, and a form that makes a workspace and signs you in
  /login       the Boathouse account sign-in
  /account     every workspace you belong to; for the ones you own: balance, card,
               keys, people, domains, tools; a switcher between them
  /join/<t>    (in main.py) an invite link sets the account password and lands here

A person has ONE Boathouse account (email + password) and belongs to any number
of workspaces. The account session is a host-only cookie on the platform apex,
so it is never sent to a tool. Tools on a workspace's own domain use that
domain's tenant session (auth.<domain>/login, same password).

Everything else in Boathouse is the `bh` command or MCP. These pages therefore
say as little as possible and mint things rather than store them: a project key
or an invite link is shown once, and nothing is emailed.

Reads the database and billing directly; never calls the API over HTTP.
main.py imports this module last, so main is imported lazily (`M = _main()`).
"""
import json
import secrets
import time
from urllib.parse import quote, urlencode

import re
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from . import checkout, resources, auth, billing, config, db, deploy, hosts, legal, mail, pages, referrals, agent_docs

router = APIRouter()
CSRF = "account"
SITE = Path(__file__).resolve().parent.parent / "site"     # the marketing site: index.html + brand/ assets


def _main():
    from . import main as M          # lazy: main imports this module at its own bottom
    return M


def _detail(e: HTTPException) -> str:
    d = e.detail
    return d if isinstance(d, str) else (d.get("error") if isinstance(d, dict) else str(d))


def _platform(request: Request):
    """The Where for a platform-host request, or None for any other host."""
    M = _main()
    w = M._where(request)
    return w if w.kind == "platform" else None


def _email(request: Request) -> str | None:
    return auth.platform_email(request.cookies.get(config.PLATFORM_COOKIE))


def _signed_in(email: str, to: str = "/account") -> RedirectResponse:
    with db.conn() as c:   # the workspace page shows "last sign-in"; a platform sign-in counts everywhere this person belongs
        c.execute("UPDATE users SET last_login=? WHERE email=?", (time.time(), email.lower()))
    resp = RedirectResponse(to, 303)
    resp.set_cookie(config.PLATFORM_COOKIE, auth.create_platform_session(email), max_age=config.SESSION_DAYS * 86400,
                    secure=True, httponly=True, samesite="lax", path="/")
    return resp


def _safe_next(url: str | None) -> str:
    return url if (url and url.startswith("/") and not url.startswith("//") and "\\" not in url
                   and not any(ord(ch) < 32 or ord(ch) == 127 for ch in url)) else "/account"


# =============================================================================
# landing, signup, sign-in, sign-out
# =============================================================================

def landing(request: Request):
    """GET / on the platform host: the marketing site when one is built, else the signup form."""
    if not _platform(request):
        return HTMLResponse(pages.unknown(_main()._host(request)), 404)
    if _main()._host(request) == "www." + config.PLATFORM_DOMAIN:
        query = "?" + request.url.query if request.url.query else ""
        return RedirectResponse(f"https://{config.PLATFORM_DOMAIN}/{query}", 308)
    index = SITE / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})
    return start(request)


@router.get("/signup")
@router.get("/start")
def start(request: Request, code: str | None = None, workspace: str = "", partner: bool = False):
    """The sign-up form. Someone already signed in gets their workspaces and a way to make another."""
    if not _platform(request):
        return HTMLResponse(pages.unknown(_main()._host(request)), 404)
    email = _email(request)
    if email and partner:
        return RedirectResponse('/partners', 303)
    if email:
        return HTMLResponse(pages.start_signed_in(email, auth.memberships(email), auth.csrf_token(CSRF), workspace, code or ""))
    return HTMLResponse(pages.signup(config.PLATFORM_DOMAIN, auth.csrf_token("signup"), name=workspace, code=code or "", partner=partner))


@router.get("/blog")
@router.get("/blog/{slug}")
def blog_page(request: Request, slug: str = ""):
    """The blog: static pages built by control/site/src/build_blog.py (platform host only)."""
    if not _platform(request):
        return HTMLResponse(pages.unknown(_main()._host(request)), 404)
    if slug and not re.fullmatch(r"[a-z0-9-]{1,80}", slug):
        raise HTTPException(404)
    f = SITE / "blog" / slug / "index.html" if slug else SITE / "blog" / "index.html"
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=300"})


@router.get("/sitemap.xml")
@router.get("/robots.txt")
def crawler_files(request: Request):
    """sitemap.xml and robots.txt for the platform host; tool hosts get a robots.txt that says stay out."""
    name = request.url.path.lstrip("/")
    if not _platform(request):
        if name == "robots.txt":
            return PlainTextResponse("User-agent: *\nDisallow: /\n")
        raise HTTPException(404)
    f = SITE / name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="application/xml" if name.endswith(".xml") else "text/plain; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/site/{path:path}")
def site_asset(path: str, request: Request):
    """Brand assets and images for the marketing site (platform host only)."""
    if not _platform(request):
        raise HTTPException(404)
    f = (SITE / path).resolve()
    if not str(f).startswith(str(SITE.resolve())) or not f.is_file() or f.name == "index.html":
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400"})


# =============================================================================
# getting connected: the installer, the bh command, the skill, the docs
# =============================================================================

def _repo_file(*parts) -> Path | None:
    """cli/ and skill/ sit beside app/ in the image (/app) and one level up in a checkout (control/app)."""
    here = Path(__file__).resolve().parent
    for root in (here.parent, here.parent.parent):
        f = root.joinpath(*parts)
        if f.is_file():
            return f
    return None


def _installer(platform: str) -> str:
    return r'''#!/bin/sh
# Boat House: install the agent command and connect this workspace.
set -e
P="@PLATFORM@"; KEY="${1:-}"
PY=""
for c in python3 python python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$("$c" -c 'import sys; print(sys.executable)')"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Boat House needs Python 3.9 or newer. Ask your agent to install a supported Python and retry this connection." >&2
  exit 1
fi
if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; elif command -v wget >/dev/null 2>&1; then GET="wget -qO-"; else echo "Ask your agent to install curl or wget, then retry." >&2; exit 1; fi
DIR="$HOME/.local/bin"; mkdir -p "$DIR"
TMP="$(mktemp "$DIR/.bh-install.XXXXXX")"
trap 'rm -f "$TMP"' EXIT HUP INT TERM
$GET "https://$P/bh" > "$TMP"
if ! head -c 40 "$TMP" | grep -q '^#!/usr/bin/env python3'; then
  echo "Could not download the Boat House command. Check the connection and retry." >&2; exit 1
fi
# Bind the interpreter we tested, even when an older python3 shadows it on PATH.
"$PY" - "$TMP" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text()
p.write_text('#!' + sys.executable + '\n' + source.split('\n', 1)[1])
PY
chmod +x "$TMP"; mv "$TMP" "$DIR/bh"
echo "Installed $DIR/bh"
if [ ! -e "$DIR/boathouse" ] && [ ! -L "$DIR/boathouse" ]; then
  ln -s bh "$DIR/boathouse"
fi
# Keep future shells convenient; the agent can use the full path immediately.
case ":$PATH:" in
  *":$DIR:"*) ;;
  *)
    for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
      if [ -f "$rc" ] && ! grep -q '.local/bin' "$rc" 2>/dev/null; then printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"; fi
    done
    if [ ! -f "$HOME/.profile" ]; then printf 'export PATH="$HOME/.local/bin:$PATH"\n' > "$HOME/.profile"; fi
    case "${SHELL:-}" in *zsh) if [ ! -f "$HOME/.zshrc" ]; then printf 'export PATH="$HOME/.local/bin:$PATH"\n' > "$HOME/.zshrc"; fi ;; esac
    ;;
esac
# Install the guide for future sessions. Read the URL now to use it in this session.
for BASE in "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.cursor/skills" "$HOME/.codeium/windsurf/skills"; do
  if mkdir -p "$BASE/boathouse" && $GET "https://$P/skill.md" > "$TMP"; then
    cp "$TMP" "$BASE/boathouse/SKILL.md"
  else
    echo "Could not save the agent guide. Read https://$P/skill.md directly for this session." >&2
  fi
done
if [ -n "$KEY" ]; then
  case "$KEY" in
    bh_*) "$DIR/bh" login "https://api.$P" "$KEY" ;;
    *)    "$DIR/bh" claim --api "https://api.$P" "$KEY" ;;
  esac
  echo "Connected. Read https://$P/skill.md now and run $DIR/bh whoami to confirm the workspace. Continue the customer's task in this conversation."
else
  echo "Installed. To connect, get a connection code at https://$P/account and run $DIR/bh claim CODE."
fi
'''.replace("@PLATFORM@", platform)


@router.get("/install.sh")
def install_sh(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    return PlainTextResponse(_installer(config.PLATFORM_DOMAIN), media_type="text/x-shellscript; charset=utf-8",
                             headers={"Cache-Control": "public, max-age=300"})


@router.get("/bh")
@router.get("/boathouse")
def cli_file(request: Request):
    f = _repo_file("cli", "bh")
    if not _platform(request) or f is None:
        raise HTTPException(404)
    return FileResponse(f, media_type="text/x-python; charset=utf-8", headers={"Cache-Control": "public, max-age=300"})


def _skill_text() -> str:
    f = _repo_file("skill", "boathouse", "SKILL.md")
    if f is None:
        raise HTTPException(404)
    return f.read_text().replace("boathousecloud.com", config.PLATFORM_DOMAIN)


@router.get("/skill.md")
def skill_md(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    return PlainTextResponse(_skill_text(), media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "public, max-age=300"})


@router.get("/preview/home")
def preview_home(request: Request):
    """A mock of the next home page, for comparing against the live one. Not linked, not indexed."""
    if not _platform(request):
        raise HTTPException(404)
    p = SITE / "home2.html"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"})


@router.get("/demo")
def demo_page(request: Request):
    """The clickable demo: a live public sample tool, the real deploy transcript, sharing, price, try-it."""
    if not _platform(request):
        raise HTTPException(404)
    f = SITE / "demo.html"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


@router.get("/security")
def security_page(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    return FileResponse(SITE / "security.html", media_type="text/html; charset=utf-8")


@router.get("/terms")
def terms_page(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    return HTMLResponse(pages.legal("Terms of Service", legal.TERMS, legal.UPDATED, config.PLATFORM_DOMAIN))


@router.get("/privacy")
def privacy_page(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    return HTMLResponse(pages.legal("Privacy Policy", legal.PRIVACY, legal.PRIVACY_UPDATED, config.PLATFORM_DOMAIN))


@router.get("/docs")
def docs_page(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    return HTMLResponse(pages.docs(agent_docs.index_markdown() + "\n" + _skill_text(), config.PLATFORM_DOMAIN))


@router.get("/docs/{slug}")
def guide_page(slug: str, request: Request):
    if not _platform(request):
        raise HTTPException(404)
    markdown = slug.endswith(".md")
    key = slug[:-3] if markdown else slug
    if key not in agent_docs.GUIDES:
        raise HTTPException(404)
    body = agent_docs.read(key)
    if markdown:
        return PlainTextResponse(body, media_type="text/markdown; charset=utf-8")
    return HTMLResponse(agent_docs.render(key, config.PLATFORM_DOMAIN))


@router.get("/llms-full.txt")
def llms_full(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    body = "# Boat House: complete agent guide\n\n" + _skill_text()
    for slug in agent_docs.GUIDES:
        body += f"\n\n---\nSource: https://{config.PLATFORM_DOMAIN}/docs/{slug}\n\n" + agent_docs.read(slug)
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")


@router.get("/.well-known/mcp-registry-auth")
def registry_proof(request: Request):
    # Only the public ownership proof is served; the signing key stays off-host.
    proof = SITE / "mcp-registry-auth.txt"
    if not _platform(request) or not proof.is_file():
        raise HTTPException(404)
    return PlainTextResponse(proof.read_text(), media_type="text/plain")


@router.get("/examples/hello/{name}")
def example_file(request: Request, name: str):
    """The example tool, fetchable by agents: the reference for verifying the signed identity headers."""
    if not _platform(request) or name not in ("app.py", "Dockerfile", "boathouse.json"):
        raise HTTPException(404)
    here = Path(__file__).resolve()
    f = next((c for c in (here.parent.parent / "examples" / "hello" / name,            # in the image: /app/examples
                          here.parent.parent.parent / "examples" / "hello" / name)     # in the repo: boathouse/examples
              if c.exists()), None)
    if not f:
        raise HTTPException(404)
    return PlainTextResponse(f.read_text(), media_type="text/plain; charset=utf-8")


@router.get("/llms.txt")
def llms_txt(request: Request):
    if not _platform(request):
        raise HTTPException(404)
    p = config.PLATFORM_DOMAIN
    return PlainTextResponse(f"""# Boat House
> The Google Doc for small software: an agent puts a tool online with one command, shares it like a document, $10 a month per organization for up to five lightweight tools.

- [How to use bh, every command](https://{p}/skill.md): the skill file, markdown
- [Complete documentation](https://{p}/llms-full.txt): every workflow in one plain-text response
- [Install in Cursor, Codex or Windsurf](https://{p}/docs/install-agent): npx skills add allenzhangyiteng/boathouse-skills
- [Create a working app](https://{p}/docs/create-app): bh init, offline team and static starters
- [Deploy a Claude Code app with team login](https://{p}/docs/deploy-claude-code-app): account, preview, deploy, verify
- [Share with teammates](https://{p}/docs/share-with-team): Viewer, Editor and Admin
- [Publish a static website](https://{p}/docs/publish-static-website): private preview to an explicitly public URL
- [Add login to an existing app](https://{p}/docs/add-team-login): gateway identity and app permissions
- [Connect a real domain](https://{p}/docs/connect-domain): existing domains and approved purchases
- [Install](https://{p}/install.sh): curl -fsSL https://{p}/install.sh | sh -s -- <one-time connection code>
- [Blog](https://{p}/blog): plain answers about hosting the tools you build with AI, with logins, domains and costs
- [Example tool](https://{p}/examples/hello/app.py): a complete tool that verifies the signed identity headers; its [Dockerfile](https://{p}/examples/hello/Dockerfile)
- First ten minutes: sign up at https://{p}/signup (no card) → confirm your email → click Copy connection on the welcome page and paste into your agent’s chat → put $20 on the balance → bh deploy in the tool's folder → bh share <tool> <email> --tier editor. Share and invite emails are sent for you.
- A machine that already has bh: bh whoami to check; otherwise bh claim <connection code>. Advanced raw-key login: bh login https://api.{p} <project key>
- MCP: https://mcp.{p}/mcp with header Authorization: Bearer <project key>
- API: https://api.{p}
- Support: support@{p}, every email answered within one business day
""", media_type="text/plain; charset=utf-8")


@router.post("/signup")
def signup(request: Request, workspace: str = Form(""), email: str = Form(""), password: str = Form(""), csrf: str = Form(""), code: str = Form(""), partner: bool = Form(False)):
    """Confirm the mailbox before an email receives account-wide authority."""
    M = _main()
    if not _platform(request):
        return HTMLResponse(pages.unknown(M._host(request)), 404)
    workspace, email = workspace.strip(), email.strip().lower()

    def again(msg: str, status: int = 400):
        return HTMLResponse(pages.signup(config.PLATFORM_DOMAIN, auth.csrf_token("signup"), msg, workspace, email, code, partner=partner), status)

    if not auth.csrf_ok(csrf, "signup"):
        return again("That form had expired, or its token did not match (a copy from another tab?). Load the page again and retry.")
    key = f"signup|{M._ip(request)}"
    if auth.throttle(key, limit=5, window=3600):
        return again("Too many signup attempts from this address. Please try again in an hour.", 429)
    if auth.has_password(email):
        if partner:
            return RedirectResponse('/login?next=%2Fpartners', 303)
        # an existing Boathouse account: the password must be theirs
        if not password:
            return _resume_signup(workspace, code)
        if not auth.check_login(email, password):
            auth.record_attempt(key)
            return again("That email already has an account. Sign in with your password or reset it to continue.", 401)
    else:
        try:
            M.validate_workspace(workspace, email, code)
        except HTTPException as e:
            return again(_detail(e), e.status_code)
        email_limit = f"signup-email|{email}"
        if auth.throttle(email_limit, limit=3):
            return again("Please wait fifteen minutes before requesting another confirmation email.", 429)
        auth.record_attempt(key)
        auth.record_attempt(email_limit)
        token = auth.signup_token(workspace, email, code, purpose='partner' if partner else '')
        url = f"https://{config.PLATFORM_DOMAIN}/verify-email/{token}"
        sent = mail.send(email, "Confirm your email for Boat House",
                         f"Confirm your email to create your Boat House account for {workspace}. Open this link and choose your password:\n\n{url}\n\n"
                         "This link expires in one hour. If you did not request an account, you can ignore this email.\n")
        if not sent:
            return again("We could not send your confirmation email. Please try again later or contact Boat House support at support@example.com.", 503)
        return HTMLResponse(pages.signup_sent(email), 202)
    auth.record_attempt(key)
    try:
        made = M.create_workspace(workspace, email, by=None, code=code)
    except HTTPException as e:
        return again(_detail(e), e.status_code if e.status_code in (409, 422) else 400)
    return _to_welcome(email, made["workspace"])


def _resume_signup(workspace: str, code: str = ""):
    destination = "/signup?" + urlencode({"workspace": workspace, "code": code})
    return RedirectResponse("/login?next=" + quote(destination, safe=""), 303)


@router.get("/verify-email/{token}")
def verify_signup_form(token: str, request: Request):
    if not _platform(request):
        raise HTTPException(404)
    pending = auth.signup_details(token)
    if not pending:
        return HTMLResponse(pages.signup_expired(), 410)
    if auth.has_password(pending["email"]):
        if pending.get('purpose') == 'partner':
            return RedirectResponse('/login?next=%2Fpartners', 303)
        return _resume_signup(pending["workspace"], pending["code"])
    return HTMLResponse(pages.verify_signup(pending["email"], pending["workspace"], auth.csrf_token("verify-email")))


@router.post("/verify-email/{token}")
def verify_signup_submit(token: str, request: Request, password: str = Form(""), password2: str = Form(""), csrf: str = Form("")):
    if not _platform(request):
        raise HTTPException(404)
    pending = auth.signup_details(token)
    if not pending:
        return HTMLResponse(pages.signup_expired(), 410)
    if auth.has_password(pending["email"]):
        if pending.get('purpose') == 'partner':
            return RedirectResponse('/login?next=%2Fpartners', 303)
        return _resume_signup(pending["workspace"], pending["code"])
    problem = "This form expired. Please try again." if not auth.csrf_ok(csrf, "verify-email") else (
        auth.password_problem(password) or ("The two passwords differ." if password != password2 else None))
    if problem:
        return HTMLResponse(pages.verify_signup(pending["email"], pending["workspace"], auth.csrf_token("verify-email"), problem), 400)
    try:
        made = _main().create_workspace(pending["workspace"], pending["email"], password=password, code=pending["code"], signup_proof=token)
    except HTTPException as e:
        return HTMLResponse(pages.signup(config.PLATFORM_DOMAIN, auth.csrf_token("signup"), _detail(e), pending["workspace"], pending["email"], pending["code"], partner=pending.get('purpose') == 'partner'), e.status_code)
    if pending.get('purpose') == 'partner':
        return _signed_in(pending['email'], '/partners')
    return _to_welcome(pending["email"], made["workspace"])


WELCOME_COOKIE = "bh_welcome"
WELCOME_TTL = 15 * 60


def _to_welcome(email: str, slug: str, name: str = "first key", joined: bool = False) -> RedirectResponse:
    """Mint the person's account key (it is them, in every workspace they belong to) and carry it to /welcome
    in a short, encrypted, host-only cookie."""
    ws = db.workspace(slug)
    key = auth.create_project_key(auth.ACCOUNT, email, name)
    code = auth.create_claim(ws["id"], email, key)
    db.audit(email, "key.create", email, {"name": name, "via": "welcome", "account": True}, ws["id"])
    resp = _signed_in(email, f"/welcome?ws={slug}" + ("&joined=1" if joined else ""))
    resp.set_cookie(WELCOME_COOKIE, auth.encrypt(json.dumps({"k": key, "c": code, "ws": slug, "t": time.time()})), max_age=WELCOME_TTL,
                    secure=True, httponly=True, samesite="lax", path="/welcome")
    return resp


def _welcome_key(request: Request, slug: str) -> tuple[str | None, str | None]:
    """(key, claim code) from the short welcome cookie, or (None, None)."""
    raw = request.cookies.get(WELCOME_COOKIE)
    if not raw:
        return None, None
    try:
        d = json.loads(auth.decrypt(raw))
    except Exception:
        return None, None
    if d.get("ws") == slug and time.time() - d.get("t", 0) < WELCOME_TTL:
        return d["k"], d.get("c")
    return None, None


def _welcome_state(ws, email: str) -> dict:
    with db.conn() as c:
        used = c.execute("""SELECT MAX(last_used) FROM project_keys WHERE email=? AND (workspace_id=? OR workspace_id=?)""", (email, ws["id"], auth.ACCOUNT)).fetchone()[0]
        tools = c.execute("SELECT * FROM tools WHERE workspace_id=? AND current_release_id IS NOT NULL ORDER BY created", (ws["id"],)).fetchall()
    base = hosts.base_for(ws)
    return {"connected": bool(used), "connected_ago": pages._ago(used), "card_on_file": bool(ws["stripe_pm"]),
            "topup_quotes": billing.form_quotes(ws, email),
            "pending_payment": billing.pending_payment(ws),
            "discount_until": ws["discount_until"], "referred_by": ws["referred_by"], "tool_day_cents": billing.daily_rate(ws), "tool_month_cents": billing.monthly_rate(ws),
            "balance_cents": billing.balance(ws["id"]), "autorefill_cents": ws["autorefill_cents"], "autorefill_cap_cents": ws["autorefill_cap_cents"],
            "tools": [{"name": t["name"], "url": hosts.tool_url(ws, t["slug"]), "open": f"/open/{ws['slug']}/{t['slug']}"} for t in tools if deploy.status(t)["state"] == "running"]}


# =============================================================================
# requests to be let in: the owner presses one button
# =============================================================================

def _request_view(request: Request, rid: str, token: str | None):
    """(email, request row, workspace, tool, None) for a signed-in decider holding the right link, else (…, response)."""
    email, mem, bad = _who(request)
    if bad:
        return None, None, None, None, bad
    M = _main()
    with db.conn() as c:
        r = c.execute("SELECT * FROM access_requests WHERE id=?", (rid,)).fetchone()
    if not r or not token or auth._token_hash(token) != r["token_hash"]:
        return None, None, None, None, HTMLResponse(pages.unknown("that request"), 404)
    with db.conn() as c:
        ws = c.execute("SELECT * FROM workspaces WHERE id=?", (r["workspace_id"],)).fetchone()
        t = c.execute("SELECT * FROM tools WHERE id=?", (r["tool_id"],)).fetchone()
    if not ws or not t:
        return None, None, None, None, HTMLResponse(pages.access_request({"state": "gone", "why": "That tool is gone.", "slug": "", "tier": r["tier"]}), 410)
    if not M._can_decide(email, ws, t):
        return None, None, None, None, HTMLResponse(pages.denied(email, config.PLATFORM_DOMAIN, M._owners(ws["id"]), f"https://{config.PLATFORM_DOMAIN}/login"), 403)
    return email, r, ws, t, None


def _request_page(email, r, ws, t, state=None, token=None):
    v = {"id": r["id"], "email": r["email"], "tier": r["tier"], "message": r["message"], "tool": t["slug"], "tool_name": t["name"],
         "slug": ws["slug"], "url": hosts.tool_url(ws, t["slug"]), "me": email, "csrf": auth.csrf_token(CSRF), "token": token or "", "state": state}
    return HTMLResponse(pages.access_request(v), 410 if state == "gone" else 200)


@router.get("/requests/{rid}")
def request_page(request: Request, rid: str, t: str | None = None):
    email, r, ws, tool, bad = _request_view(request, rid, t)
    if bad:
        return bad
    if r["decided"]:
        return _request_page(email, r, ws, tool, "gone")
    if r["expires"] < time.time():
        return _request_page(email, r, ws, tool, "gone")
    return _request_page(email, r, ws, tool, None, t)


def _decide(request: Request, rid: str, csrf: str, t: str, decision: str):
    email, r, ws, tool, bad = _request_view(request, rid, t)
    if bad:
        return bad
    if not auth.csrf_ok(csrf, CSRF):
        return HTMLResponse(pages.unknown("an expired form; open the link again"), 400)
    try:
        _main()._decide_request(r, decision, email)
    except HTTPException:
        return _request_page(email, r, ws, tool, "gone")
    return _request_page(email, r, ws, tool, decision)


@router.post("/requests/{rid}/allow")
def request_allow(request: Request, rid: str, csrf: str = Form(""), t: str = Form("")):
    return _decide(request, rid, csrf, t, "allowed")


@router.post("/requests/{rid}/decline")
def request_decline(request: Request, rid: str, csrf: str = Form(""), t: str = Form("")):
    return _decide(request, rid, csrf, t, "declined")


@router.get("/open/{slug}/{tool}")
def open_tool(request: Request, slug: str, tool: str):
    """One click from the welcome or account page into a tool, no second password: a 60-second, single-use, signed
    handoff to the workspace's own sign-in host, minted at click time (never a long-lived link sitting on a page)."""
    if not _platform(request):
        raise HTTPException(404)
    email, mem, bad = _who(request)
    if bad:
        return bad
    m = _pick(mem, slug)
    if not m:
        return RedirectResponse("/account", 302)
    w = db.workspace_by_id(m["id"])
    M = _main()
    t = M._tool_by_slug(w["id"], tool)
    if not t or not M._tier_for(M._user(w["id"], email), t):
        return RedirectResponse(f"/account?ws={w['slug']}", 302)
    tok = auth.sign({"handoff": w["id"], "email": email, "to": hosts.tool_url(w, t["slug"]),
                     "exp": time.time() + 60, "n": secrets.token_urlsafe(8), "pw": auth.credential_stamp(email)})
    return RedirectResponse(f"https://auth.{hosts.base_for(w)}/handoff?t={tok}", 303)


@router.get("/welcome")
def welcome(request: Request, ws: str | None = None, card: str | None = None, topped: str | None = None, refill: str | None = None, joined: str | None = None, want: str | None = None):
    email, mem, bad = _who(request)
    if bad:
        return bad
    m = _pick(mem, ws)
    if not m:
        return RedirectResponse("/account", 302)
    w = db.workspace_by_id(m["id"])
    if m["my_role"] != "owner":
        # someone shared in: open what was shared, connect an agent if they can change things, close the tab
        M = _main()
        user = M._user(w["id"], email)
        base = hosts.base_for(w)
        with db.conn() as c:
            tools = c.execute("SELECT * FROM tools WHERE workspace_id=? ORDER BY name", (w["id"],)).fetchall()
        shown = []
        for t in tools:
            tier = M._tier_for(user, t)
            if tier:
                url = hosts.tool_url(w, t["slug"])
                shown.append({"slug": t["slug"], "name": t["name"], "url": url, "open": f"/open/{w['slug']}/{t['slug']}", "tier": tier[0]})
        key, code = _welcome_key(request, w["slug"])
        v = {"csrf": auth.csrf_token(CSRF), "slug": w["slug"], "name": w["name"], "platform": config.PLATFORM_DOMAIN, "email": email,
             "key": key, "code": code, "tools": shown, "role": m["my_role"],
             "can_change": m["my_role"] == "owner" or any(t.get("tier") == "admin" for t in shown)}
        return HTMLResponse(pages.welcome_shared(v))
    key, code = _welcome_key(request, w["slug"])
    notice = None
    if card == "saved":
        notice = "Card saved. Nothing is charged until you say so."
    elif card == "cancelled":
        notice = "No card was saved. You can add one any time."
    elif topped:
        notice = "Your current balance and payment history are shown below."
    elif refill == "on":
        notice = "Auto-refill is on. You will see every refill on the ledger."
    elif refill == "off":
        notice = "Auto-refill is off."
    v = {"csrf": auth.csrf_token(CSRF), "slug": w["slug"], "name": w["name"], "platform": config.PLATFORM_DOMAIN,
         "key": key, "code": code, "email": email, "notice": notice, "want": want, "referral": referrals.summary(email), **_welcome_state(w, email)}
    return HTMLResponse(pages.welcome(v))


@router.get("/welcome/status")
def welcome_status(request: Request, ws: str | None = None):
    email, mem, bad = _who(request)
    if bad:
        return JSONResponse({"error": "sign in"}, 401)
    m = _pick(mem, ws)
    if not m or m["my_role"] != "owner":
        return JSONResponse({"error": "not an owner"}, 403)
    return JSONResponse(_welcome_state(db.workspace_by_id(m["id"]), email))


def login_form(request: Request, next: str | None = None):
    """Called by main.login_form on the platform host."""
    if _email(request):
        return RedirectResponse(_safe_next(next), 302)
    return HTMLResponse(pages.platform_login(config.PLATFORM_DOMAIN, auth.csrf_token("plogin"), _safe_next(next)))


def login_submit(request: Request, email: str = "", password: str = "", next: str = "", csrf: str = ""):
    """Called by main.login_submit on the platform host."""
    M = _main()
    email = email.strip().lower()
    nxt = _safe_next(next)
    key = f"{M._ip(request)}|{email}"

    def again(msg, code=400):
        return HTMLResponse(pages.platform_login(config.PLATFORM_DOMAIN, auth.csrf_token("plogin"), nxt, msg, email), code)

    if not auth.csrf_ok(csrf, "plogin"):
        return again("That form had expired, or its token did not match (a copy from another tab?). Load the page again and retry.")
    if auth.throttle(key):
        db.audit(email, "login.throttled", None, {"ip": M._ip(request), "on": "platform"})
        return again("Too many attempts. Wait fifteen minutes.", 429)
    if not auth.check_login(email, password):
        auth.record_attempt(key)
        db.audit(email, "login.failed", None, {"ip": M._ip(request), "on": "platform"})
        return again("That email and password do not match. Try again, reset your password, or create an account if you’re new.", 401)
    db.audit(email, "login.ok", None, {"ip": M._ip(request), "on": "platform"})
    return _signed_in(email, nxt)


def platform_logout(request: Request):
    auth.delete_session(request.cookies.get(config.PLATFORM_COOKIE))
    resp = RedirectResponse("/", 302)
    resp.delete_cookie(config.PLATFORM_COOKIE, path="/")
    return resp


# =============================================================================
# the account page
# =============================================================================

def _who(request: Request):
    """(email, memberships, None) for a signed-in person on the platform host, else (None, None, response)."""
    M = _main()
    w = M._where(request)
    if w.kind == "home" and w.workspace:
        # the old address: send people to the real one
        return None, None, RedirectResponse(f"https://{config.PLATFORM_DOMAIN}/account?ws={w.workspace['slug']}", 302)
    if w.kind != "platform":
        return None, None, HTMLResponse(pages.unknown(M._host(request)), 404)
    email = _email(request)
    if not email:
        here = quote(str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""), safe="/?=&")
        return None, None, RedirectResponse(f"/login?next={here}", 302)
    return email, auth.memberships(email), None


def _pick(memberships, slug: str | None):
    for m in memberships:
        if m["slug"] == slug:
            return m
    return memberships[0] if memberships else None


def _owner(request: Request, slug: str):
    """(email, workspace row, None) for a signed-in owner of that workspace, else (None, None, response)."""
    email, mem, bad = _who(request)
    if bad:
        return None, None, bad
    m = next((x for x in mem if x["slug"] == slug), None)
    if not m:
        return None, None, HTMLResponse(pages.unknown(f"workspace {slug}"), 404)
    if m["my_role"] != "owner":
        return None, None, HTMLResponse(pages.account_member(_member_view(email, mem, m)), 403)
    return email, m, None


def _member_view(email: str, memberships, m) -> dict:
    M = _main()
    user = M._user(m["id"], email)
    base = hosts.base_for(m)
    with db.conn() as c:
        tools = c.execute("SELECT * FROM tools WHERE workspace_id=? ORDER BY name", (m["id"],)).fetchall()
    shown = []
    for t in tools:
        tier = M._tier_for(user, t)
        if not tier:
            continue
        shown.append({"name": t["name"], "url": f"https://{t['slug']}.{base}", "open": f"/open/{m['slug']}/{t['slug']}", "my_tier": tier[0]})
    return {"email": email, "memberships": memberships, "slug": m["slug"], "name": m["name"], "my_role": m["my_role"], "tools": shown,
            "referral": referrals.summary(email),
            "can_mint": _can_mint(email, m), "csrf": auth.csrf_token(CSRF), "platform": config.PLATFORM_DOMAIN}


def _view(email: str, memberships, m, notice: str | None = None, error: str | None = None) -> dict:
    """Everything the owner page shows, read straight from the tables."""
    ws = db.workspace_by_id(m["id"])                    # re-read: an action may have moved the balance
    base = hosts.base_for(ws)
    with db.conn() as c:
        tools = c.execute("SELECT * FROM tools WHERE workspace_id=? ORDER BY name", (ws["id"],)).fetchall()
        keys = [dict(r) for r in c.execute(
            """SELECT id, email, name, created, last_used, workspace_id FROM project_keys
               WHERE workspace_id=? OR (workspace_id=? AND email IN (SELECT email FROM users WHERE workspace_id=?)) ORDER BY created DESC""",
            (ws["id"], auth.ACCOUNT, ws["id"]))]
        people = [dict(r) | {"has_password": auth.has_password(r["email"])} for r in c.execute(
            "SELECT email, name, role, created, last_login FROM users WHERE workspace_id=? ORDER BY role, email", (ws["id"],))]
    shown = []
    running = 0
    for t in tools:
        state = deploy.status(t)["state"]
        running += state == "running"
        with db.conn() as c:
            rel = c.execute("SELECT seq, note FROM releases WHERE id=?", (t["current_release_id"],)).fetchone()
            grants = [dict(g) for g in c.execute("SELECT email, tier, labels FROM grants WHERE tool_id=? ORDER BY email", (t["id"],))]
        shown.append({"slug": t["slug"], "name": t["name"], "url": f"https://{t['slug']}.{base}", "open": f"/open/{ws['slug']}/{t['slug']}", "state": state,
                      "release": dict(rel) if rel else None, "default_access": t["default_access"],
                      "default_tier": t["default_tier"], "grants": grants, "usage": resources.current(t) if config.RESOURCE_GUARD else None})
    balance = billing.balance(ws["id"])
    burn = billing.daily_rate(ws) if running else 0
    return {
        "csrf": auth.csrf_token(CSRF), "notice": notice, "error": error,
        "memberships": memberships, "my_role": "owner",
        "name": ws["name"], "slug": ws["slug"], "base": base, "platform": config.PLATFORM_DOMAIN, "email": email,
        "balance_cents": balance, "paused": bool(ws["paused"]), "card_on_file": bool(ws["stripe_pm"]),
        "topup_quotes": billing.form_quotes(ws, email),
        "pending_payment": billing.pending_payment(ws),
        "autorefill_cents": ws["autorefill_cents"], "autorefill_cap_cents": ws["autorefill_cap_cents"],
        "running": running, "burn_cents": burn, "days_left": (balance // burn) if burn else None,
        "organization_usage": resources.organization_usage(ws['id']),
        "ledger": billing.ledger(ws["id"], 10), "keys": keys, "people": people,
        "free_address": f"{ws['slug']}.{config.PLATFORM_DOMAIN}",
        "domains": [dict(d) for d in hosts.workspace_domains(ws["id"])], "tools": shown,
        "referral": referrals.summary(email), "discount_until": ws["discount_until"], "referred_by": ws["referred_by"],
        "tool_day_cents": billing.daily_rate(ws), "tool_month_cents": billing.monthly_rate(ws),
    }


def _render(email, memberships, m, notice=None, error=None, code=200):
    return HTMLResponse(pages.account(_view(email, memberships, m, notice, error)), code)


@router.get("/account")
def account(request: Request, ws: str | None = None, welcome: str | None = None, card: str | None = None, topped: str | None = None, refill: str | None = None):
    email, mem, bad = _who(request)
    if bad:
        return bad
    if not mem:
        return HTMLResponse(pages.no_workspaces(email, auth.csrf_token(CSRF)))
    m = _pick(mem, ws)
    if m["my_role"] != "owner":
        return HTMLResponse(pages.account_member(_member_view(email, mem, m)))
    notice = None
    if welcome:
        notice = f"Welcome. {m['name']} is ready. Get a connection code below and paste it into your agent’s chat, or use the welcome page."
    elif card == "saved":
        notice = "Your payment methods are shown below."
    elif card == "cancelled":
        notice = "No card was saved."
    elif topped:
        notice = "Your current balance and payment history are shown below."
    elif refill == "on":
        notice = "Auto-refill is on."
    elif refill == "off":
        notice = "Auto-refill is off."
    return _render(email, mem, m, notice=notice)


def _guard(request: Request, slug: str, csrf: str):
    """(email, memberships, workspace, None) once the caller is an owner with a live form."""
    email, m, bad = _owner(request, slug)
    if bad:
        return None, None, None, bad
    mem = auth.memberships(email)
    if not auth.csrf_ok(csrf, CSRF):
        return None, None, None, _render(email, mem, m, error="That form had expired. Load the page again and retry.", code=400)
    return email, mem, m, None


@router.post("/account/new-workspace")
def account_new_workspace(request: Request, workspace: str = Form(""), csrf: str = Form(""), code: str = Form("")):
    email, mem, bad = _who(request)
    if bad:
        return bad
    if not auth.csrf_ok(csrf, CSRF):
        return RedirectResponse("/account", 303)
    try:
        made = _main().create_workspace(workspace, email, by=email, code=code)
    except HTTPException as e:
        if mem:
            return _render(email, mem, _pick(mem, None), error=_detail(e), code=e.status_code if e.status_code in (409, 422) else 400)
        return HTMLResponse(pages.no_workspaces(email, auth.csrf_token(CSRF)), 400)
    return RedirectResponse(f"/account?ws={made['workspace']}&welcome=1", 303)


# ---- money -------------------------------------------------------------------------

@router.post("/account/{slug}/card")
def account_card(slug: str, request: Request, csrf: str = Form(""), back: str = Form(""), want: str = Form("")):
    email, mem, ws, bad = _guard(request, slug, csrf)
    if bad:
        return bad
    where = f"https://{config.PLATFORM_DOMAIN}/welcome?ws={ws['slug']}" if back == "welcome" else f"https://{config.PLATFORM_DOMAIN}/account?ws={ws['slug']}"
    if want == "domain":
        where += "&want=domain"
    try:
        url = billing.card_link(ws, where, email)
    except billing.StripeError as e:
        return _render(email, mem, ws, error=str(e), code=422)
    db.audit(email, "billing.card_link", None, {"via": "account page"}, ws["id"])
    return RedirectResponse(url, 303)


@router.post("/account/{slug}/topup")
def account_topup(slug: str, request: Request, dollars: str = Form("20"), csrf: str = Form(""), back: str = Form(""), operation_id: str = Form("")):
    """A top-up from the page: the owner's own click charges the card on file. No agent is involved, ever."""
    email, mem, ws, bad = _guard(request, slug, csrf)
    if bad:
        return bad
    try:
        cents = billing.dollars_to_cents(dollars)
    except billing.StripeError:
        cents = 0
    to = f"/welcome?ws={ws['slug']}" if back == "welcome" else f"/account?ws={ws['slug']}"
    if cents < 500 or cents > 100000:
        return _render(email, mem, ws, error="Top up between $5 and $1,000.", code=422)
    try:
        result = checkout.start(ws, cents, email, operation_id)
    except billing.StripeError as e:
        return _render(email, mem, ws, error=str(e), code=422)
    if result.get('checkout_url'):
        return RedirectResponse(result['checkout_url'],303)
    if result['payment_status']=='succeeded':
        return _render(email,mem,ws,notice=f"Payment confirmed. Your balance is ${result['balance_cents']/100:.2f}.")
    return _render(email,mem,ws,notice='This checkout has ended or is still processing. Your balance below reflects confirmed payments only.')


@router.get('/account/{slug}/payment-return')
def account_payment_return(slug: str, request: Request, operation_id: str = ''):
    email,ws,bad = _owner(request,slug)
    if bad: return bad
    mem=auth.memberships(email)
    try:
        result=checkout.reconcile(ws,operation_id)
    except billing.StripeError as e:
        return _render(email,mem,ws,error=str(e),code=422)
    message=(f"Payment confirmed. Your balance is ${result['balance_cents']/100:.2f}." if result['payment_status']=='succeeded'
             else 'Your payment is still being confirmed. Credit will appear here after Stripe confirms it.')
    return _render(email,mem,ws,notice=message)


@router.post("/account/{slug}/autorefill")
def account_autorefill(slug: str, request: Request, dollars: str = Form("20"), cap: str = Form("100"), csrf: str = Form(""), back: str = Form("")):
    """Auto-refill, set from the page: the card is charged this much whenever the balance runs low, never more than the cap a month."""
    email, mem, ws, bad = _guard(request, slug, csrf)
    if bad:
        return bad
    try:
        cents, cap_cents = billing.dollars_to_cents(dollars), billing.dollars_to_cents(cap)
    except billing.StripeError:
        cents, cap_cents = -1, 0
    to = f"/welcome?ws={ws['slug']}" if back == "welcome" else f"/account?ws={ws['slug']}"
    try:
        billing.set_auto_refill(ws, cents, cap_cents)
    except billing.StripeError as e:
        return _render(email, mem, ws, error=str(e), code=422)
    db.audit(email, "billing.autorefill", None, {"cents": cents, "cap_cents": cap_cents, "via": "page"}, ws["id"])
    return RedirectResponse(to + ("&refill=on" if cents else "&refill=off"), 303)


# ---- the agent's key ----------------------------------------------------------------

def _can_mint(email: str, m) -> bool:
    """Owners and members mint their own keys; so does a guest who is admin on some tool here (a shared-in editor of the software)."""
    if m["my_role"] in ("owner", "member"):
        return True
    with db.conn() as c:
        return bool(c.execute("SELECT 1 FROM grants g JOIN tools t ON t.id=g.tool_id WHERE t.workspace_id=? AND g.email=? AND g.tier='admin'",
                              (m["id"], email)).fetchone())


@router.post("/account/{slug}/token")
def account_token(slug: str, request: Request, name: str = Form("agent"), csrf: str = Form(""), back: str = Form("")):
    email, mem, bad = _who(request)
    if bad:
        return bad
    ws = next((x for x in mem if x["slug"] == slug), None)
    if not ws:
        return HTMLResponse(pages.unknown(f"workspace {slug}"), 404)
    if not auth.csrf_ok(csrf, CSRF):
        return HTMLResponse(pages.account_member(_member_view(email, mem, ws)) if ws["my_role"] != "owner" else pages.account(_view(email, mem, ws, error="That form had expired. Load the page again and retry.")), 400)
    if not _can_mint(email, ws):
        return HTMLResponse(pages.account_member(_member_view(email, mem, ws)), 403)
    name = (name or "agent").strip()[:60] or "agent"
    if back != "welcome":
        key = auth.create_project_key(auth.ACCOUNT, email, name)
        code = auth.create_claim(ws["id"], email, key)
        db.audit(email, "key.create", email, {"name": name, "via": "account page", "account": True}, ws["id"])
        return HTMLResponse(pages.new_key(key, name, email, config.PLATFORM_DOMAIN, ws["slug"], code))
    if back == "welcome":
        return _to_welcome(email, ws["slug"], name)
    key = auth.create_project_key(ws["id"], email, name)
    code = auth.create_claim(ws["id"], email, key)
    db.audit(email, "key.create", email, {"name": name, "via": "account page"}, ws["id"])
    return HTMLResponse(pages.new_key(key, name, email, config.PLATFORM_DOMAIN, ws["slug"], code))


@router.post("/account/{slug}/keys/{key_id}/revoke")
def account_revoke_key(slug: str, key_id: str, request: Request, csrf: str = Form("")):
    email, mem, ws, bad = _guard(request, slug, csrf)
    if bad:
        return bad
    with db.conn() as c:
        k = c.execute("""SELECT * FROM project_keys WHERE id=? AND (workspace_id=? OR (workspace_id=? AND email IN (SELECT email FROM users WHERE workspace_id=?)))""",
                      (key_id, ws["id"], auth.ACCOUNT, ws["id"])).fetchone()
        if not k:
            return _render(email, mem, ws, error="That key is already gone.", code=404)
        if k["workspace_id"] == auth.ACCOUNT and k["email"] != email:
            return _render(email, mem, ws, error=f"That key is {k['email']}'s own, and works in their other workspaces too; only they can revoke it. Remove them from this workspace instead.", code=403)
        c.execute("DELETE FROM project_keys WHERE id=?", (key_id,))
    db.audit(email, "key.revoke", key_id, {"name": k["name"], "via": "account page"}, ws["id"])
    return _render(email, mem, ws, notice=f"Key {k['name']} revoked. Anything using it stops working now.")


# ---- people ------------------------------------------------------------------------

@router.post("/account/{slug}/people")
def account_add_person(slug: str, request: Request, email_: str = Form("", alias="email"), role: str = Form("member"), csrf: str = Form("")):
    email, mem, ws, bad = _guard(request, slug, csrf)
    if bad:
        return bad
    M = _main()
    target, role = email_.strip().lower(), (role or "member").strip()
    if "@" not in target or "." not in target.split("@")[-1]:
        return _render(email, mem, ws, error="That does not look like an email address.", code=422)
    if role not in ("owner", "member", "guest"):
        return _render(email, mem, ws, error="Role must be member, guest or owner.", code=422)
    person, invite = M._ensure_user(ws, target, role, email)
    db.audit(email, "user.set", target, {"role": role, "via": "account page"}, ws["id"])
    if not invite:
        url, emailed = M._existing_member_notice(ws, target, email)
        return HTMLResponse(pages.once("Workspace access", "Open the workspace",
                            f"{target} is now a {role}. They can sign in with their usual Boat House password. "
                            + ("We emailed them the link." if emailed else "Send them this sign-in link."), url, "", ws["slug"]))
    emailed = mail.invite_notice(target, email, ws["name"], role, invite)
    return HTMLResponse(pages.once(
        "Invite link", "Send this to " + target,
        f"{target} is a {role} of this workspace. " + ("We emailed their secure sign-in link. You can also share this invitation." if emailed else
        "Email delivery is unavailable. This invitation lets them request a secure link once email is restored."),
        invite, "", ws["slug"]))


@router.post("/account/{slug}/invite/{target}")
def account_invite(slug: str, target: str, request: Request, csrf: str = Form("")):
    email, mem, ws, bad = _guard(request, slug, csrf)
    if bad:
        return bad
    M = _main()
    target = target.strip().lower()
    if not M._user(ws["id"], target):
        return _render(email, mem, ws, error=f"{target} is not in this workspace.", code=404)
    if auth.has_password(target):
        url, emailed = M._existing_member_notice(ws, target, email)
        return HTMLResponse(pages.once("Workspace access", "Open the workspace",
                            f"{target} already has a Boat House account. They can use their usual password, "
                            "or reset it from the sign-in page. " + ("We emailed them the link." if emailed else "Send them this sign-in link."),
                            url, "", ws["slug"]))
    url = M._invite_url(ws, auth.create_invite(ws["id"], target, email))
    emailed = mail.invite_notice(target, email, ws["name"], M._user(ws["id"], target)["role"], url)
    db.audit(email, "invite.create", target, {"via": "account page"}, ws["id"])
    return HTMLResponse(pages.once(
        "Invite link", "Send this to " + target,
        f"A fresh invitation for {target}. " + ("We emailed their secure sign-in link." if emailed else
        "Email delivery is unavailable. They can request a secure link here once email is restored."),
        url, "", ws["slug"]))


@router.post("/account/{slug}/people/{target}/remove")
def account_remove_person(slug: str, target: str, request: Request, csrf: str = Form("")):
    email, mem, ws, bad = _guard(request, slug, csrf)
    if bad:
        return bad
    target = target.strip().lower()
    if target == email:
        return _render(email, mem, ws, error="You cannot remove yourself. Another owner has to do that.", code=422)
    M = _main()
    if not M._user(ws["id"], target):
        return _render(email, mem, ws, error=f"{target} is not in this workspace.", code=404)
    wid = ws["id"]
    with db.conn() as c:
        c.execute("DELETE FROM users WHERE workspace_id=? AND email=?", (wid, target))
        c.execute("DELETE FROM sessions WHERE workspace_id=? AND email=?", (wid, target))
        c.execute("DELETE FROM project_keys WHERE workspace_id=? AND email=?", (wid, target))
        c.execute("DELETE FROM invites WHERE workspace_id=? AND email=?", (wid, target))
        c.execute("DELETE FROM grants WHERE email=? AND tool_id IN (SELECT id FROM tools WHERE workspace_id=?)", (target, wid))
    db.audit(email, "user.remove", target, {"via": "account page"}, wid)
    return _render(email, mem, ws, notice=f"{target} removed. Their sessions, keys, invites and shares went with them.")


@router.post('/account/{slug}/apps/{tool}/capacity')
def account_capacity(slug: str,tool: str,request: Request,csrf: str=Form(''),storage_gb: int=Form(2),quote_id: str=Form(''),maximum: int=Form(-1)):
    email,mem,ws,bad=_guard(request,slug,csrf)
    if bad: return bad
    t=_main()._tool_or_404(ws['id'],tool)
    try:
        if quote_id:
            resources.confirm(t,ws,quote_id,maximum,email)
            return _render(email,mem,ws,notice='Capacity increased. No one-time charge was made; approved extra storage is metered as used.')
        q=resources.quote(t,ws,storage_gb,email)
        body=f'''<h1>Review capacity for {pages._e(t['name'])}</h1><p>{pages._e(q['message'].replace('Confirm only after the owner approves.',''))}</p>
<form method=post><input type=hidden name=csrf value="{pages._e(csrf)}"><input type=hidden name=quote_id value="{pages._e(q['quote_id'])}"><input type=hidden name=maximum value="{q['max_extra_monthly_cents']}"><button>Approve up to ${q['max_extra_monthly_cents']/100:.2f} extra per month</button></form><p><a href="/account?ws={pages._e(slug)}">Cancel</a></p>'''
        return HTMLResponse(pages.page('Review app capacity',body))
    except resources.ResourceError as e: return _render(email,mem,ws,error=str(e),code=409)


@router.get("/account/{slug}/domains")
def renewal_page(slug: str, request: Request, saved: str = ""):
    from . import domain_renewals
    email, ws, bad = _owner(request, slug)
    if bad:
        return bad
    return HTMLResponse(pages.domain_renewals_page(slug, domain_renewals.settings(ws['id']), auth.csrf_token(CSRF), bool(saved)))


@router.post("/account/{slug}/domains/{domain}/renewal")
def renewal_form(slug: str, domain: str, request: Request, csrf: str = Form(""), enabled: str = Form("off"), maximum: str = Form("")):
    from decimal import Decimal, InvalidOperation
    from . import domain_renewals
    email, mem, ws, bad = _guard(request,slug,csrf)
    if bad:
        return bad
    try:
        if enabled not in ('on','off'):
            raise HTTPException(422,'Choose on or off.')
        amount = Decimal(maximum or '0') * 100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise InvalidOperation
        domain_renewals.set_policy(ws['id'], _main()._valid_domain(domain), enabled=='on', int(amount), email)
    except (InvalidOperation, ValueError, HTTPException) as exc:
        detail = exc.detail if isinstance(exc,HTTPException) else 'Enter a price in dollars and cents.'
        return HTMLResponse(pages.page('Renewal settings', f'<h1>Settings were not changed</h1><p>{pages._e(str(detail))}</p><p><a href="/account/{pages._e(slug)}/domains">Back to domains</a></p>'),status_code=exc.status_code if isinstance(exc,HTTPException) else 422)
    return RedirectResponse(f'/account/{slug}/domains?saved=1',303)
