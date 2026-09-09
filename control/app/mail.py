"""Email, the one kind Boat House sends: "someone shared a tool with you", with the link and three steps.
Two ways out of the box: HTTPS through Resend (works on clouds that block SMTP ports, which is most of them),
or SMTP through the host's own mailbox (Google Workspace with an app password). Settings live encrypted
beside the Stripe keys. When nothing is configured, callers fall back to printing the link; nothing breaks."""
import html as _h
import json
import logging
import smtplib
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, quote
from email.message import EmailMessage

from . import auth, config, db

log = logging.getLogger("boathouse.mail")
FIELDS = ("provider", "host", "port", "user", "password", "from", "api_key")
RESEND = "https://api.resend.com"


def settings() -> dict | None:
    with db.conn() as c:
        rows = {r["name"]: auth.decrypt(r["value_enc"]) for r in c.execute("SELECT name, value_enc FROM settings WHERE name LIKE 'mail.%'")}
    got = {f: rows.get(f"mail.{f}") for f in FIELDS}
    got["provider"] = got["provider"] or "smtp"
    if got["provider"] == "resend":
        return got if got["api_key"] and got["from"] else None
    return got if got["host"] and got["user"] and got["password"] else None


def configured() -> bool:
    return settings() is not None


def _put(pairs: dict, by: str | None):
    with db.conn() as c:
        for name, val in pairs.items():
            c.execute("""INSERT INTO settings VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET value_enc=excluded.value_enc,
                         updated=excluded.updated, updated_by=excluded.updated_by""", (f"mail.{name}", auth.encrypt((val or "").strip()), time.time(), by))


def set_smtp(host: str, port: int, user: str, password: str, sender: str, by: str | None):
    _put({"provider": "smtp", "host": host, "port": str(port), "user": user, "password": password, "from": sender or user}, by)


def set_resend(api_key: str, sender: str, by: str | None):
    _put({"provider": "resend", "api_key": api_key, "from": sender}, by)


def _resend(method: str, path: str, body: dict | None = None, api_key: str | None = None) -> dict:
    """One call to Resend's HTTPS API. Raises RuntimeError with Resend's own message on any refusal."""
    key = api_key or (settings() or {}).get("api_key") or ""
    req = urllib.request.Request(RESEND + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "boathouse/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message") or e.reason
        except Exception:   # noqa: BLE001
            msg = e.reason
        raise RuntimeError(f"Resend said: {msg}") from None


def send(to: str, subject: str, text: str, html: str | None = None, strict: bool = False, reply_to: str | None = None) -> bool:
    """True when the mail left the box. Never raises unless strict: sharing must work with or without email."""
    st = settings()
    if not st:
        return False
    try:
        if st["provider"] == "resend":
            body = {"from": f"Boat House <{st['from']}>", "to": [to], "subject": subject, "text": text, "reply_to": reply_to or st["from"]}
            if html:
                body["html"] = html
            _resend("POST", "/emails", body)
        else:
            msg = EmailMessage()
            msg["From"] = f"Boat House <{st['from']}>"
            msg["To"] = to
            msg["Subject"] = subject
            msg["Reply-To"] = reply_to or st["from"]
            msg.set_content(text)
            if html:
                msg.add_alternative(html, subtype="html")
            with smtplib.SMTP(st["host"], int(st["port"] or 587), timeout=25) as smtp:
                smtp.starttls()
                smtp.login(st["user"], st["password"])
                smtp.send_message(msg)
        return True
    except Exception as e:      # noqa: BLE001 - any failure just means "send the link by hand"
        log.warning("mail to %s failed: %s", to, e)
        if strict:
            raise
        return False


def sending_domain() -> str:
    st = settings() or {}
    return (st.get("from") or "").rsplit("@", 1)[-1].lower()


def resend_domain_records(domain: str) -> tuple[str, str, list[dict]]:
    """Register the sending domain at Resend (or find it) and return (id, status, the DNS records it wants).
    Record names come back relative to the zone, the way registrars want them."""
    found = None
    try:
        found = _resend("POST", "/domains", {"name": domain, "region": "us-east-1"})
    except RuntimeError as e:
        if "already" not in str(e).lower() and "exist" not in str(e).lower():
            raise
    if not found:
        for d in _resend("GET", "/domains").get("data", []):
            if d.get("name", "").lower() == domain:
                found = _resend("GET", f"/domains/{d['id']}")
                break
    if not found:
        raise RuntimeError(f"Resend did not accept the domain {domain}")
    recs = []
    for r in found.get("records", []):
        name = (r.get("name") or "").lower()
        if name.endswith("." + domain):
            name = name[: -len(domain) - 1]
        elif name == domain:
            name = ""
        recs.append({"name": name, "type": r.get("type", "").upper(), "content": r.get("value", ""), "prio": r.get("priority"), "what": r.get("record", "")})
    return found["id"], found.get("status", "not_started"), recs


def resend_domain_status(domain_id: str) -> str:
    try:
        _resend("POST", f"/domains/{domain_id}/verify")
    except RuntimeError:
        pass
    return _resend("GET", f"/domains/{domain_id}").get("status", "pending")


def _steps(tier: str | None, link: str | None, tool_url: str, workspace: str, slug: str) -> list[str]:
    """The three things to do, in plain words. The same list is printed as text and drawn as HTML."""
    if link:
        steps = ["Open this link and choose a password. The link works once and expires in seven days. The password is yours from then on."]
        if tier == "admin":
            steps.append("On the page that opens, click Copy connection and paste into your Claude Code, Codex or Cursor chat. Your agent handles setup. "
                         "From then on your agent can work on this tool and anything else shared with you. "
                         "Your agent may ask you to approve setup; you do not need to configure anything.")
            steps.append(f"Tell your agent what to change, in plain words. The tool's short name is {workspace}/{slug}. For example: "
                         f"\"On {workspace}/{slug}, change the page heading to 'Welcome' and put it live.\"")
        else:
            steps.append(f"You land on the tool. Bookmark it: {tool_url}")
            steps.append("That is all. Sign in with your email and the password you chose whenever you come back.")
    else:
        steps = [f"Open it, signed in with your usual Boat House password: {tool_url}"]
        if tier == "admin":
            steps.append(f"If your AI agent is already connected to Boat House, it can work on it now: say \"On {workspace}/{slug}, ...\". "
                         f"If not, sign in at https://{config.PLATFORM_DOMAIN}/account, open {workspace}, and click Get a connection code under Your agent. Copy connection, then paste into your agent’s chat.")
            steps.append("Every change you put live is a version; the owner can put any earlier one back.")
        else:
            steps.append("That is all.")
    return steps


def _text(steps: list[str], link: str | None) -> str:
    out = []
    for i, s in enumerate(steps, 1):
        out.append(f"{i}. {s}" + (f"\n   {link}" if i == 1 and link else ""))
    return "\n".join(out)


FOOT = "Boat House is the Google Doc for small software: an AI agent puts a tool online, and it is shared like a document. Every email to support@example.com is answered within one business day."


FONT = "font-family:Inter,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"


def _html(title: str, intro: str, button: str, url: str, steps: list[str], foot: str, preheader: str = "") -> str:
    """One card, one button, the numbered steps. Inline styles, tables and the font repeated on every element, because email clients."""
    e = _h.escape
    rows = "".join(
        f'<tr><td style="padding:14px 0 0"><table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td valign="top" style="width:26px;padding-top:1px"><span style="display:inline-block;width:24px;height:24px;line-height:24px;border-radius:12px;'
        f'border:1px solid #A9C7FA;color:#307BF9;font-size:13px;text-align:center;font-family:Menlo,Consolas,monospace">{i}</span></td>'
        f'<td style="padding-left:12px;font-size:15px;line-height:1.5;color:#425466;{FONT}">{e(s)}</td></tr></table></td></tr>'
        for i, s in enumerate(steps, 1))
    spacer = "&#847;&zwnj;&nbsp;" * 40
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{e(title)}</title>
<style>:root{{color-scheme:light}} @media (max-width:600px){{.card{{padding:24px 20px 22px !important}} .h1{{font-size:22px !important}}}}</style></head>
<body style="margin:0;padding:0;background:#F8FAFD;{FONT};color:#14263F">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:#F8FAFD;mso-hide:all">{e(preheader)}{spacer}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F8FAFD"><tr><td align="center" style="padding:36px 16px 40px">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:560px">
  <tr><td style="padding:0 4px 18px;{FONT}">
    <img src="https://{config.PLATFORM_DOMAIN}/site/brand/icon-192.png" width="26" height="26" alt="" style="vertical-align:middle;border-radius:6px;border:0">
    <span style="font-size:15px;font-weight:500;color:#14263F;vertical-align:middle;padding-left:8px;{FONT}">Boat House</span>
  </td></tr>
  <tr><td class="card" style="background:#ffffff;border:1px solid #E5EDF5;border-radius:6px;padding:36px 36px 30px">
    <h1 class="h1" style="margin:0 0 12px;font-size:26px;line-height:1.2;font-weight:400;letter-spacing:-.01em;color:#14263F;{FONT}">{e(title)}</h1>
    <p style="margin:0 0 26px;font-size:16px;line-height:1.5;color:#425466;{FONT}">{e(intro)}</p>
    <table role="presentation" cellpadding="0" cellspacing="0"><tr><td bgcolor="#307BF9" style="border-radius:4px;background:#307BF9">
      <a href="{e(url)}" style="display:block;color:#ffffff;text-decoration:none;font-size:16px;line-height:1;padding:15px 24px 16px;border-radius:4px;{FONT}">{e(button)}</a>
    </td></tr></table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #E5EDF5;margin-top:30px">
      <tr><td style="padding:18px 0 0;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:#64748D;font-family:Menlo,Consolas,monospace">What to do</td></tr>
      {rows}
    </table>
  </td></tr>
  <tr><td style="padding:20px 4px 0;font-size:13px;line-height:1.55;color:#8593A6;{FONT}">{e(foot)}</td></tr>
</table></td></tr></table></body></html>"""


def share_notice(to: str, sharer: str, tool_name: str, slug: str, tool_url: str, tier: str, link: str | None, workspace: str) -> bool:
    link = _mailbox_invite(link)
    what = {"viewer": "look at it", "editor": "change the data inside it",
            "admin": "change anything about it, including the software, the way you would edit a shared document"}[tier]
    an = "an" if tier[0] in "ae" else "a"
    intro = f'{sharer} shared "{tool_name}" with you on Boat House, as {an} {tier}: you can {what}.'
    steps = _steps(tier, link, tool_url, workspace, slug)
    text = f"{intro}\n\n{_text(steps, link)}\n\n{FOOT}\nQuestions? Reply to this email.\n"
    html = _html(f'"{tool_name}" is now shared with you', intro, f"Open {tool_name}", link or tool_url, steps,
                 f"{FOOT} Questions? Reply to this email and {sharer} will see it.",
                 "Open it, choose a password, and you are in." if link else "Open it with your usual password.")
    return send(to, f'{sharer} shared "{tool_name}" with you', text, html, reply_to=sharer)


def invite_notice(to: str, inviter: str, workspace_name: str, role: str, link: str) -> bool:
    link = _mailbox_invite(link)
    an = "an" if role[0] in "aeiou" else "a"
    intro = f'{inviter} added you to "{workspace_name}" on Boat House as {an} {role}.'
    steps = ["Open this link and choose a password. The link works once and expires in seven days. The password is yours from then on.",
             "You land on the workspace, with the tools you can open.",
             "To work on them with your AI agent (Claude Code, Cursor, Codex): the page shows one line to paste into it."]
    text = f"{intro}\n\n{_text(steps, link)}\n\n{FOOT}\nQuestions? Reply to this email.\n"
    html = _html(f'You are in "{workspace_name}"', intro, f"Open {workspace_name}", link, steps,
                 f"{FOOT} Questions? Reply to this email and {inviter} will see it.", "Open it, choose a password, and you are in.")
    return send(to, f'{inviter} added you to "{workspace_name}" on Boat House', text, html, reply_to=inviter if "@" in inviter else None)


def _mailbox_invite(link: str | None) -> str | None:
    if not link:
        return None
    token = urlparse(link).path.rsplit("/", 1)[-1]
    proof = auth.invite_proof(token)
    return f"{link}?verify={quote(proof, safe='')}" if proof else link


def request_notice(to: str, requester: str, tool_name: str, slug: str, tier: str, link: str, workspace_name: str, message: str | None) -> bool:
    """Someone asks to be let in. One button, Allow; the owner never types a command."""
    what = {"viewer": "look at it", "editor": "change the data inside it",
            "admin": "change anything about it, including the software, the way you would edit a shared document"}[tier]
    an = "an" if tier[0] in "ae" else "a"
    intro = f'{requester} asks to be {an} {tier} on "{tool_name}" in {workspace_name}, which means they could {what}.'
    if message:
        intro += f' They say: "{message}"'
    steps = ["Press Allow. If you are not signed in, you sign in first and land back here.",
             f"{requester} gets an email saying what to do next, and can start right away.",
             f"Change your mind any time: bh unshare {slug} {requester}, or on your workspace page."]
    text = f"{intro}\n\nAllow it here (the link works for seven days):\n   {link}\n\n{_text(steps, None)}\n\n{FOOT}\n"
    html = _html(f'{requester} asks to open "{tool_name}"', intro, "Allow", link, steps,
                 f"{FOOT} Questions? Reply to this email and {requester} will see it.", f"{requester} asks for {tier} access. One button.")
    return send(to, f'{requester} asks to open "{tool_name}"', text, html, reply_to=requester)


def test_notice(to: str) -> bool:
    intro = "This is the test message from bh mail test. If you are reading it, the emails Boat House sends when someone shares a tool will arrive too."
    steps = ["Nothing. This one is only a test.", "Shares and invites now go out on their own, with one button and three steps like these.",
             "Reply to this email to check that replies reach the right inbox."]
    text = f"{intro}\n\n{_text(steps, None)}\n"
    return send(to, "Boat House can send email", text, _html("Boat House can send email", intro, "Open Boat House", f"https://{config.PLATFORM_DOMAIN}/account", steps, FOOT), strict=True)
