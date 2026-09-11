"""Every human-facing page the control plane serves, in the site's own design: start, sign in, the welcome
flow after signup, the workspace page, invite/join, and the small tenant pages (denied, paused, down).
One stylesheet, no framework; a few lines of script only where a page needs to copy or poll."""
import datetime as _dt
import html
import json
import re
import time

MARK = ('<svg class="mark" viewBox="0 0 772 404" aria-hidden="true"><polygon points="0,224 164,400 408,404 524,386" fill="#307BF9"/>'
        '<polygon points="24,220 334,184 304,296" fill="#79AAFB"/><polygon points="352,200 420,272 366,312 316,294" fill="#AECFFD"/>'
        '<polygon points="368,176 772,0 440,258" fill="#8CB5FB"/><polygon points="772,0 554,368 400,324" fill="#307BF9"/></svg>')

STYLE = """<style>
:root{--ink:#14263F;--body:#425466;--muted:#64748D;--hair:#E5EDF5;--band:#F8FAFD;--accent:#307BF9;--accent-deep:#1F62D6;--accent-border:#A9C7FA;--tint:#EAF2FF;--good:#1A7C4F;--good-dot:#22A06B;--bad:#B42318;--sans:"Helvetica Neue",Helvetica,Arial,sans-serif;--mono:"SF Mono",Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);font:16px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
.top{height:64px;border-bottom:1px solid var(--hair);display:flex;align-items:center;padding:0 32px;gap:24px}
.brand{display:flex;align-items:center;gap:10px;color:var(--ink);text-decoration:none;font-weight:500;font-size:15px;letter-spacing:-.01em}
.mark{width:28px;height:auto;display:block}
.top nav{margin-left:auto;display:flex;gap:20px;align-items:center;font-size:14px}
.top nav a{color:var(--body);text-decoration:none}.top nav a:hover{color:var(--accent)}
.top nav a.btn{color:#fff;padding:9px 16px;font-size:14px}
main{max-width:34rem;margin:64px auto;padding:0 24px}
main.wide{max-width:1120px;margin:40px auto}
h1{font-size:32px;font-weight:300;letter-spacing:-.02em;line-height:1.1;margin:0 0 12px}
h1 .muted{color:var(--muted)}
h2{font-size:22px;font-weight:300;letter-spacing:-.01em;margin:48px 0 12px}
h3{font-size:16px;font-weight:500;margin:0}
p{color:var(--body);margin:0 0 14px}
.lead{font-size:18px}
.muted{color:var(--muted)}
small{color:var(--muted);font-size:13px}
a{color:var(--accent);text-decoration:none}
form{display:grid;gap:14px;margin:24px 0}
label{display:grid;gap:6px;font-size:13px;color:var(--muted)}
input,select{font:inherit;font-size:15px;padding:10px 12px;border:1px solid var(--hair);border-radius:4px;background:#fff;color:var(--ink);min-width:0}
input:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:var(--accent)}
button,a.btn,.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--accent);color:#fff;border:0;text-decoration:none;padding:12px 20px;border-radius:4px;font:inherit;font-size:15px;cursor:pointer;justify-self:start;white-space:nowrap}
button:hover,a.btn:hover{background:var(--accent-deep)}
button.s,a.btn.s{background:#fff;color:var(--accent);border:1px solid var(--accent-border)}
button.s:hover,a.btn.s:hover{background:var(--tint)}
.err{color:var(--bad)}.ok{color:var(--good)}
.notice{border:1px solid var(--hair);border-left:3px solid var(--accent);background:var(--band);padding:10px 14px;border-radius:4px;color:var(--ink);margin:0 0 20px}
.notice.bad{border-left-color:var(--bad)}
code{font-family:var(--mono);font-size:.92em;background:var(--band);border:1px solid var(--hair);padding:1px 6px;border-radius:4px}
.once{display:block;word-break:break-all;padding:12px 14px;margin:8px 0;font-size:14px;line-height:1.5}
.copy{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;background:var(--ink);color:#E4EBEC;border-radius:6px;padding:14px 16px;font-family:var(--mono);font-size:13.5px;line-height:1.55;word-break:break-all;margin:10px 0;min-width:0}
.copy textarea{grid-column:1/-1;width:100%;min-height:12rem;font:13px/1.5 var(--mono)}
.copy textarea[hidden]{display:none}
.copy button{background:rgba(255,255,255,.12);color:#fff;padding:6px 12px;font-size:13px;border:1px solid rgba(255,255,255,.22)}
.copy button:hover{background:rgba(255,255,255,.22)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(300px,100%),1fr));gap:16px;margin:12px 0}
.card{border:1px solid var(--hair);border-radius:6px;padding:18px 20px;background:#fff;display:flex;flex-direction:column;gap:8px}
.card .url{font-family:var(--mono);font-size:12.5px;word-break:break-all}
.card .meta{font-size:13px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good-dot);margin-right:6px;vertical-align:middle}
.dot.off{background:#B7C1D0}.dot.wait{background:#F0B95A;animation:pulse 1.4s infinite}
@keyframes pulse{50%{opacity:.35}}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0 24px;border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);padding:20px 0;margin:8px 0 24px}
.strip b{display:block;font-size:26px;font-weight:300;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.strip span{font-size:13px;color:var(--muted)}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:14px}
main.wide table{display:block;overflow-x:auto}
main.wide table>tbody:only-child{display:table;width:100%}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--hair);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12px;letter-spacing:.04em;text-transform:uppercase}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
form.inline{display:inline;margin:0}form.inline button{padding:5px 10px;font-size:13px}
form.add{grid-auto-flow:column;grid-auto-columns:1fr;align-items:end;gap:10px}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
ul.tools{list-style:none;padding:0;margin:18px 0}
ul.tools li{border:1px solid var(--hair);border-radius:6px;padding:14px 16px;margin-bottom:10px;display:flex;justify-content:space-between;gap:12px;align-items:center}
.steps{margin:24px 0;border-top:1px solid var(--hair)}
.step{display:grid;grid-template-columns:40px 1fr;gap:16px;padding:26px 0;border-bottom:1px solid var(--hair)}
.step>div{min-width:0}
.step .n{width:32px;height:32px;border:1px solid var(--hair);border-radius:50%;display:grid;place-items:center;font-size:13px;color:var(--accent);background:#fff}
.step.done .n{background:var(--good);border-color:var(--good);color:#fff}
.step h3{font-size:18px;font-weight:400;margin-bottom:4px}
.tabs{display:flex;gap:6px;margin:14px 0 6px;flex-wrap:wrap}
.tabs button{background:#fff;color:var(--body);border:1px solid var(--hair);padding:7px 12px;font-size:13px}
.tabs button.on{border-color:var(--accent);color:var(--accent);background:var(--tint)}
.pane{display:none}.pane.on{display:block}
.status{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--muted);margin-top:12px}
details{margin:8px 0}details summary{cursor:pointer;color:var(--muted);font-size:14px}
.sw{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:14px;color:var(--muted)}
.sw a,.sw strong{padding:4px 10px;border:1px solid var(--hair);border-radius:999px;font-weight:400}
.sw strong{border-color:var(--accent);color:var(--accent)}
@media(max-width:640px){.top{padding:0 16px}main{margin:40px auto}form.add{grid-auto-flow:row}.top nav{gap:12px}.step{grid-template-columns:32px 1fr;gap:12px}}

:root{--ink:#101116;--body:#62666f;--muted:#62666f;--hair:#e5e7ec;--band:#f5f6f8;--accent:#2546ff;--accent-deep:#1836d9}
h1,h2{font-weight:600;letter-spacing:-.035em}button,a.btn{border-radius:6px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:4px}
.onboarding h1{font-size:36px;line-height:1.13}.onboarding .lead{font-size:17px;line-height:1.6}.flow-progress{font-size:12px;color:var(--muted);margin-bottom:19px}.onboarding form{gap:16px}.onboarding input{padding:12px;border-color:#d8dbe3;border-radius:6px}.onboarding form>button{width:100%;min-height:47px}.onboarding .optional-field{margin:0}.optional-field label{margin-top:14px}.form-note{font-size:12px;line-height:1.6;margin:0}.auth-switch{padding-top:20px;border-top:1px solid var(--hair);font-size:14px}.auth-fineprint{font-size:12px;line-height:1.7;margin-top:21px}.forgot-link{font-size:13px;justify-self:end;margin-top:-6px}.onboarding .copy{background:var(--band);color:var(--ink);font-family:var(--sans);padding:15px;font-size:14px;border:1px solid var(--hair)}.onboarding .copy button{background:var(--accent);color:#fff;border:0;padding:10px 15px;min-height:40px}.onboarding .copy textarea{background:#fff;color:var(--ink);border:1px solid var(--hair);padding:12px}.onboarding .step h3{font-weight:500;line-height:1.4}.onboarding .step p{font-size:14px;line-height:1.7}.onboarding .step small{font-size:12px}.onboarding details p{margin-top:12px}.billing-setup>summary{display:inline-block;color:var(--accent);border:1px solid #d8dbe3;border-radius:6px;padding:10px 15px;font-weight:500;list-style:none}.billing-setup>summary::-webkit-details-marker{display:none}.billing-setup[open]>summary{margin-bottom:15px}.onboarding .status{font-size:12px}.onboarding .step.done>.n{background:#1a7c4f}
@media(max-width:640px){.onboarding h1{font-size:31px}.top{gap:12px}.top nav{font-size:12px}.onboarding .copy{grid-template-columns:1fr}.onboarding .copy button{justify-self:stretch}.onboarding .copy span{font-size:12px}.onboarding ul.tools li{align-items:flex-start;flex-wrap:wrap}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
</style>"""

SCRIPT = """<script>
(function(){
  document.querySelectorAll('.tabs').forEach(function(t){t.querySelectorAll('button').forEach(function(b){b.addEventListener('click',function(){
    t.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===b)});
    var host=t.parentNode;host.querySelectorAll('.pane').forEach(function(p){p.classList.toggle('on',p.dataset.t===b.dataset.t)});
  })})});
  document.querySelectorAll('.copy button').forEach(function(b){b.addEventListener('click',function(){
    var box=b.parentNode,source=box.querySelector('textarea'),text=source?source.value:box.querySelector('span').textContent,label=b.textContent;
    (navigator.clipboard?navigator.clipboard.writeText(text):Promise.reject()).then(function(){b.textContent='Copied';setTimeout(function(){b.textContent=label},1500)},function(){
      if(!source){source=document.createElement('textarea');source.value=text;source.readOnly=true;box.appendChild(source)}
      source.hidden=false;source.focus();source.select();b.textContent='Selected — copy this';
    });
  })});
  var w=document.getElementById('welcome');
  if(w){var ws=w.dataset.ws,tries=0;function poll(){fetch('/welcome/status?ws='+encodeURIComponent(ws),{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(s){
      var c=document.getElementById('connect');if(s.connected&&c){c.classList.add('done');document.getElementById('cstat').textContent='Connected. Your agent is ready.';}
      var t=document.getElementById('tstat');if(t&&s.tools&&s.tools.length){
        var u=new URL(s.tools[0].open||s.tools[0].url,location.origin);t.textContent='Your app is online: ';
        if(u.protocol==='https:'||u.protocol==='http:'){var a=document.createElement('a');a.href=u.href;a.textContent=s.tools[0].name;t.appendChild(a)}
      }
      var k=document.getElementById('card');if(s.balance_cents>0&&k&&!k.classList.contains('done')){k.classList.add('done');location.reload();}
      if((!s.connected||!s.tools.length)&&tries++<400)setTimeout(poll,3000);
    }).catch(function(){if(tries++<400)setTimeout(poll,5000)})}
    poll();}
})();
</script>"""


def _e(s) -> str:
    return html.escape(str(s) if s is not None else "")


def page(title: str, body: str, nav: str = "", wide: bool = False, *, description: str = "", canonical: str = "") -> str:
    metadata = (f'<meta name="description" content="{_e(description)}"><link rel="canonical" href="{_e(canonical)}">'
                if canonical else '<meta name="robots" content="noindex, follow">')
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)}</title>{metadata}<link rel=icon href=/site/brand/favicon.svg type=image/svg+xml>{STYLE}</head><body>"
            f"<header class=top><a class=brand href=/>{MARK}Boat House</a><nav>{nav}</nav></header>"
            f"<main class='{'wide' if wide else ''}'>{body}</main>{SCRIPT}</body></html>")


def _money(cents) -> str:
    cents = cents or 0
    return f"{'-' if cents < 0 else ''}${abs(cents) / 100:,.2f}"


def _date(ts) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d") if ts else "—"


def _stamp(ts) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "never"


def _ago(ts) -> str:
    if not ts:
        return "never"
    s = max(0, int(_dt.datetime.now(_dt.timezone.utc).timestamp() - ts))
    for n, w in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if s >= n:
            k = s // n
            return f"{k} {w}{'' if k == 1 else 's'} ago"
    return "just now"


# ---- tenant pages: sign in on a workspace domain, join from an invite, the tool list, the small refusals ------

def login(base: str, next_url: str, csrf: str, error: str | None = None, email: str = "") -> str:
    err = f"<p class=err>{_e(error)}</p>" if error else ""
    return page("Sign in", f"""<h1>Sign in</h1><p>To the tools on <code>{_e(base)}</code>.</p>{err}
<form method=post action=/login>
<input type=hidden name=next value="{_e(next_url)}"><input type=hidden name=csrf value="{_e(csrf)}">
<label>Email<input type=email name=email value="{_e(email)}" required autofocus autocomplete=username></label>
<label>Password<input type=password name=password required autocomplete=current-password></label>
<button>Sign in</button></form>
<p><small><a href="/forgot-password">Forgot your password?</a> No account yet? Ask the workspace owner for an invite.</small></p>""")


def join(email: str, base: str, csrf: str, error: str | None = None, name: str = "") -> str:
    err = f"<p class=err>{_e(error)}</p>" if error else ""
    return page("Set your password", f"""<h1>Welcome. <span class=muted>Choose a password.</span></h1>
<p>You have been added to <code>{_e(base)}</code> as <code>{_e(email)}</code>. This is the only setup step there is.</p>{err}
<form method=post>
<input type=hidden name=csrf value="{_e(csrf)}">
<label>Your name<input name=name value="{_e(name)}" autocomplete=name></label>
<label>Password (12+ characters)<input type=password name=password required minlength=12 autocomplete=new-password autofocus></label>
<label>Again<input type=password name=password2 required minlength=12 autocomplete=new-password></label>
<button>Set password and sign in</button></form>""")


def invite_dead() -> str:
    return page("Invitation unavailable", '<h1>This invitation is no longer available</h1><p>If you already have an account, <a href="/login">sign in</a> or <a href="/forgot-password">reset your password</a>.</p><p>Otherwise, ask the person who invited you to send a new invitation.</p>')


def invite_email(email: str, csrf: str, message: str | None = None, sent: bool = False) -> str:
    note = f"<p>{_e(message)}</p>" if message else ""
    form = "" if sent else f'<form method=post><input type=hidden name=csrf value="{_e(csrf)}"><button>Email me a secure link</button></form>'
    return page("Confirm your email", f'<h1>You are invited</h1><p>To get started, open the secure link sent to {_e(email)}. Only that mailbox can activate this account.</p>{note}{form}')


def signup_sent(email: str) -> str:
    return page("Check your email", f'<div class=onboarding><p class=flow-progress>Step 2 of 3 · Confirm your email</p><h1>Check your email</h1><p>We sent a secure link to <strong>{_e(email)}</strong>. Open it to choose your password and finish creating your account.</p><p>The link works for one hour. Check your spam folder if it hasn’t arrived.</p><p><a href="/signup">Use a different email or send another link</a></p><p class=auth-fineprint>Need a hand? <a href="mailto:support@example.com">Email support</a>.</p></div>')


def verify_signup(email: str, workspace: str, csrf: str, error: str | None = None) -> str:
    note = f'<p class=err role=alert>{_e(error)}</p>' if error else ""
    return page("Choose your Boat House password", f'''<div class=onboarding><p class=flow-progress>Step 2 of 3 · Email confirmed</p><h1>Choose your password</h1><p>Use this to sign in as <strong>{_e(email)}</strong>.</p>{note}
<form method=post><input type=hidden name=csrf value="{_e(csrf)}">
<label>Password<input type=password name=password required minlength=12 autocomplete=new-password autofocus aria-describedby=password-help></label><small id=password-help>Use at least 12 characters.</small>
<label>Confirm password<input type=password name=password2 required minlength=12 autocomplete=new-password></label>
<button>Continue to my apps</button></form></div>''')


def signup_expired() -> str:
    return page("Confirmation link expired", '<h1>This confirmation link has expired or was already used</h1><p><a href="/signup">Send a new confirmation link</a> or <a href="/login">sign in</a>.</p>')


def forgot_password(csrf: str, message: str | None = None, sent: bool = False) -> str:
    note = f'<p class="{"" if sent else "err"}">{_e(message)}</p>' if message else ""
    form = "" if sent else f"""<form method=post action=/forgot-password>
<input type=hidden name=csrf value="{_e(csrf)}">
<label>Email<input type=email name=email required autofocus autocomplete=username></label>
<button>Email me a reset link</button></form>"""
    return page("Reset your password", f"""<h1>Reset your password</h1>
<p>We will email you a link to choose a new password.</p>{note}{form}
<p><a href="/login">Back to sign in</a></p>""")


def reset_password(csrf: str, error: str | None = None) -> str:
    note = f'<p class=err>{_e(error)}</p>' if error else ""
    return page("Choose a new password", f"""<h1>Choose a new password</h1>{note}
<form method=post><input type=hidden name=csrf value="{_e(csrf)}">
<label>New password (12+ characters)<input type=password name=password required minlength=12 autocomplete=new-password autofocus></label>
<label>Again<input type=password name=password2 required minlength=12 autocomplete=new-password></label>
<button>Save password and sign in</button></form>
<p><small>This signs out your other devices. Reconnect your agent from your account page afterward.</small></p>""")


def password_reset_expired() -> str:
    return page("Reset link expired", '<h1>This reset link has expired or was already used</h1><p><a href="/forgot-password">Email me a new reset link</a></p>')


def denied(email: str, host: str, owners: list[str], auth_host: str) -> str:
    who = " or ".join(_e(o) for o in owners) or "the workspace owner"
    return page("No access", f"<h1>You are signed in, but not on the list for this tool</h1><p>Signed in as <code>{_e(email)}</code>. <code>{_e(host)}</code> is not shared with you. Ask {who} to add you.</p><p><a href='https://{_e(auth_host)}/logout'>Sign out</a></p>")


def view_only(host: str) -> str:
    return page("View only", f"<h1>You can look, but not change this</h1><p>Your access to <code>{_e(host)}</code> is view only, so the change you just tried was not saved. Ask whoever runs it for editor access.</p>")


def paused(host: str, owners: list[str]) -> str:
    who = " or ".join(_e(o) for o in owners) or "the workspace owner"
    return page("Paused", f"<h1>This workspace is paused</h1><p><code>{_e(host)}</code> is stopped because the workspace balance reached zero. Nothing was deleted. {who} can top up (<code>bh billing topup</code>) and everything resumes by itself.</p>")


def down(host: str) -> str:
    return page("Tool is down", f"<h1>This tool is not running right now</h1><p><code>{_e(host)}</code> exists but its process is not answering. Whoever deploys it can run <code>bh logs</code> and <code>bh rollback</code>, or hand it to an agent.</p>")


def unknown(host: str) -> str:
    return page("No such tool", f"<h1>Nothing lives at this address</h1><p><code>{_e(host)}</code> is not a tool on this Boat House.</p>")


def home(user, tools: list[dict], base: str) -> str:
    items = "".join(
        f"<li><span><a href='https://{t['slug']}.{base}/'>{_e(t['name'])}</a><br><small>{t['slug']}.{base} · you are {_e(t['role'])}</small></span>"
        f"<small><span class='dot {'' if t['state'] == 'running' else 'off'}'></span>{_e(t['state'])}</small></li>"
        for t in tools) or "<li><small>No tools shared with you yet.</small></li>"
    return page("Your tools", f"<h1>Your tools</h1><p>Signed in as <code>{_e(user['email'])}</code> ({_e(user['role'])}).</p><ul class=tools>{items}</ul>",
                nav=f"<a href='https://auth.{_e(base)}/logout'>Sign out</a>")


def platform() -> str:
    return page("Boat House", "<h1>Boat House</h1><p>The Google Doc for small software.</p><p><small>If you were sent here to open a tool, use the link you were given; it names the workspace.</small></p>")


# ---- the platform host: start, sign in, welcome, the workspace page ------------------------------------------

NAV_OUT = '<a href="/#product">How it works</a><a href="/docs">Docs</a><a href="/login">Sign in</a>'


def _doors(which: str) -> str:
    if which == "up":
        return '<p class="auth-switch">Already have an account? <a href="/login">Sign in</a></p>'
    return '<p class="auth-switch">New to Boat House? <a href="/signup">Create an account</a></p>'


def signup(platform: str, csrf: str, error: str | None = None, name: str = "", email: str = "", code: str = "", partner: bool = False) -> str:
    err = f'<p class=err role=alert>{_e(error)}</p>' if error else ""
    referral_open = " open" if code else ""
    result = page("Create your Boat House account", f'''<div class=onboarding>
<p class=flow-progress>Step 1 of 3 · Your account</p>
<h1>Create your account</h1><p class=lead>Get your app online and share it with your team.</p>{err}
<form method=post action=/signup>
<input type=hidden name=csrf value="{_e(csrf)}">
<label>Email<input type=email name=email value="{_e(email)}" required autofocus autocomplete=email placeholder="you@example.com"></label>
<label>App or business name<input name=workspace value="{_e(name)}" required minlength=3 maxlength=80 autocomplete=organization placeholder="e.g. Oak Street Studio" aria-describedby=account-name-help></label>
<small id=account-name-help>A name for your apps in Boat House. You can keep several apps together here.</small>
<details class=optional-field{referral_open}><summary>Have a referral code?</summary><label>Referral code<input name=code value="{_e(code)}" placeholder="BH-XXXXXXXXXX" autocomplete=off style="text-transform:uppercase"><small>Half price on every tool for your first 60 days.</small></label></details>
<button>Create account</button><p class=form-note>We’ll email you a secure link to finish. No payment card needed.</p>
</form>{_doors("up")}
<p class=auth-fineprint>Managed hosting starts at $10 per app per month. Add credit when you’re ready to publish. By continuing, you agree to our <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>.</p>
</div>''', nav='<a href="/demo">How it works</a><a href="mailto:support@example.com">Get help</a>')
    if partner:
        result = result.replace('Step 1 of 3 · Your account', 'Partner program · Free to join').replace('Create your account</h1>', 'Become a Boat House partner</h1>')
        result = result.replace('Get your app online and share it with your team.', 'Get your own link, QR code, and referral code. Earn 10% for the lifetime of each customer you bring in.')
        result = result.replace('<form method=post action=/signup>', '<form method=post action=/signup><input type=hidden name=partner value=1>')
        result = result.replace('App or business name<input', 'Business or display name<input').replace('A name for your apps in Boat House. You can keep several apps together here.', 'A name for your partner account. No app or hosting purchase required.')
        result = result.replace('Managed hosting starts at $10 per app per month. Add credit when you’re ready to publish.', 'Joining is free. Payouts are reviewed monthly, with a $10 minimum and smaller balances rolling over.')
        result = result.replace('href="/login">Sign in', 'href="/login?next=%2Fpartners">Sign in')
    return result


def start_signed_in(email: str, memberships: list, csrf: str, workspace: str = "", code: str = "") -> str:
    mine = "".join(f'<li><a href="/account?ws={_e(m["slug"])}">{_e(m["name"])}</a><a class="btn s" href="/account?ws={_e(m["slug"])}">Open apps</a></li>' for m in memberships)
    show_new = " open" if workspace or not memberships else ""
    return page("Your apps", f'''<div class=onboarding><h1>Your apps</h1><p>You’re already signed in as {_e(email)}.</p>
<ul class=tools>{mine or '<li>No apps yet. Add your app or business name below to get started.</li>'}</ul>
<details{show_new}><summary>Add a separate business or team</summary><p>Keep its apps, people, and billing separate from your other apps.</p>
<form method=post action="/account/new-workspace"><input type=hidden name=csrf value="{_e(csrf)}"><input type=hidden name=code value="{_e(code)}">
<label>App or business name<input name=workspace value="{_e(workspace)}" required minlength=3 maxlength=80 placeholder="e.g. Oak Street Studio"></label><button>Add business or team</button></form></details>
<p class=auth-fineprint>Using a different account? <a href="/logout">Sign out</a>.</p></div>''', nav='<a href="/account">My apps</a><a href="mailto:support@example.com">Get help</a>')


def platform_login(platform: str, csrf: str, next_url: str, error: str | None = None, email: str = "") -> str:
    err = f'<p class=err role=alert>{_e(error)}</p>' if error else ""
    return page("Sign in to Boat House", f'''<div class=onboarding><h1>Welcome back</h1><p class=lead>Sign in to open your apps and manage your account.</p>{err}
<form method=post action=/login>
<input type=hidden name=next value="{_e(next_url)}"><input type=hidden name=csrf value="{_e(csrf)}">
<label>Email<input type=email name=email value="{_e(email)}" required autofocus autocomplete=username></label>
<label>Password<input type=password name=password required autocomplete=current-password></label>
<a class=forgot-link href="/forgot-password">Forgot your password?</a><button>Sign in</button></form>
{_doors("in")}</div>''', nav='<a href="/demo">How it works</a><a href="mailto:support@example.com">Get help</a>')


def no_workspaces(email: str, csrf: str) -> str:
    return page("Your apps", f'''<div class=onboarding><h1>Let’s get your app online</h1><p>You’re signed in as {_e(email)}. Give your apps a name to get started.</p>
<form method=post action=/account/new-workspace><input type=hidden name=csrf value="{_e(csrf)}"><label>App or business name<input name=workspace required minlength=3 maxlength=80 placeholder="e.g. Oak Street Studio"></label><button>Continue</button></form></div>''', nav='<a href="/logout">Sign out</a>')


def _connect(platform: str, key: str, code: str | None = None, workspace: str = "", email: str = "") -> str:
    """One customer action; the clipboard carries the instructions the agent needs now."""
    p = _e(platform)
    credential = code or key
    line = f"curl -fsSL https://{platform}/install.sh | sh -s -- {credential}"
    prompt = (f"Connect my Boat House workspace so you can manage my tools for me. "
              f"Connect as {email or 'the account that issued this code'} in workspace {workspace or 'the workspace that issued this code'}. "
              f"The one-time connection code is {credential}. Run this setup command with my approval where required:\n"
              f"{line}\n\n"
              f"Then read https://{platform}/skill.md in this conversation immediately, and run "
              f"~/.local/bin/bh whoami to verify the connection and selected workspace. "
              f"Keep working in this conversation; you do not need me to restart the agent or configure MCP. "
              f"Use the existing project and our conversation to work out what to do next. "
              f"Handle setup, deployment, checks, and errors yourself, and show me the working link. "
              f"Ask me only for missing decisions you cannot infer or approvals that are actually required. "
              f"Never request my password or payment details in chat. "
              f"If this code has expired or was used, first check whether bh is already connected to the right "
              f"account and workspace; otherwise send me to https://{platform}/account for a new connection code. "
              f"Do not change agent security settings or bypass an approval denial.")
    mcp = _e(json.dumps({"mcpServers": {"boathouse": {"type": "http", "url": f"https://mcp.{platform}/mcp", "headers": {"Authorization": f"Bearer {key}"}}}}))
    return f"""<p>Copy this message and paste it into <strong>Claude Code, Codex, or Cursor</strong>, in the conversation that has your app’s files. Your agent handles the setup.</p>
<div class=copy><span>Your private connection</span><textarea hidden readonly aria-label="Connection instructions">{_e(prompt)}</textarea><button type=button>Copy connection</button></div>
<p><small>Use it within 15 minutes. It works once. Keep it private until your agent is connected. Your agent may ask you to approve setup.</small></p>
<details><summary>Advanced connection options</summary><p>For agents with command access, the same installer is below. If your agent only supports MCP, use this connection configuration.</p>
<div class=copy><span>{_e(line)}</span><button type=button>Copy</button></div>
<div class=copy><span>{mcp}</span><button type=button>Copy</button></div>
<p><small>The MCP credential stays valid until you revoke it. Treat it like a password. MCP tools accept a workspace name to select where they act.</small></p></details>"""


def _month_rate(v: dict) -> str:
    month = int(v.get("tool_month_cents") or 1000)
    return "$10 a month" if month >= 1000 else f"$5 a month (half price until {_date(v.get('discount_until'))}, then $10)"


def _months20(v: dict) -> str:
    """How long $20 runs one tool at this workspace's own rate (half price while a referral discount lasts)."""
    month = int(v.get("tool_month_cents") or 1000)
    n = max(1, 2000 // month)
    return {1: "one month", 2: "two months", 3: "three months", 4: "four months"}.get(n, f"{n} months")


def _money_block(v: dict, back: str) -> str:
    """One primary funding action; preserve explicit payment and auto-refill consent."""
    a = f"/account/{_e(v['slug'])}"
    csrf = f'<input type=hidden name=csrf value="{_e(v["csrf"])}"><input type=hidden name=back value="{back}">'
    quotes = v.get("topup_quotes", {})
    pending = v.get("pending_payment")
    recovery = ""
    if pending:
        recovery = f'<p role=status>Continue or check your previous {_money(pending["cents"])} payment. Another payment waits until this one is paid or expires (30 minutes).</p>'
        if pending["kind"] == "manual":
            recovery += f'<form method=post action="{a}/topup">{csrf}<input type=hidden name=dollars value="{pending["cents"]/100:.2f}"><input type=hidden name=operation_id value="{_e(pending["id"])}"><button>Check the previous payment</button></form>'
    refill = v.get("autorefill_cents") or 0
    cap = v.get("autorefill_cap_cents") or 0
    refill_line = (f'Auto-refill adds {_money(refill)} when credit falls below $5, with a {_money(cap)} monthly cap.' if refill else 'Auto-refill is off. Every top-up needs your approval on Stripe.')
    refill_form = (f'<form method=post action="{a}/autorefill">{csrf}<input type=hidden name=dollars value=20><input type=hidden name=cap value=100><button class=s>{"Keep" if refill else "Enable"} auto-refill: $20, up to $100/month</button></form>' if v.get('card_on_file') else '<p>Optional auto-refill becomes available after your first successful card payment.</p>')
    return f'''{recovery}<p>Add $20 to run one app for about {_months20(v)}.</p>
<form method=post action="{a}/topup">{csrf}<input type=hidden name=dollars value=20><input type=hidden name=operation_id value="{_e(quotes.get('2000', ''))}"><button{' disabled' if not quotes.get('2000') else ''}>Add $20 hosting credit</button></form>
<p><small>Continue to Stripe to securely pay $20. Complete any bank verification there; you return here when done. Credit pays for your running apps each day. If it runs out, apps pause; their data is kept.</small></p>
<details><summary>More credit and optional auto-refill</summary><p>{refill_line}</p>
<form method=post action="{a}/topup">{csrf}<input type=hidden name=dollars value=40><input type=hidden name=operation_id value="{_e(quotes.get('4000', ''))}"><button class=s{' disabled' if not quotes.get('4000') else ''}>Add $40 credit</button></form>
{refill_form}
{f'<form method=post action="{a}/autorefill">{csrf}<input type=hidden name=dollars value=0><input type=hidden name=cap value=0><button class=s>Turn auto-refill off</button></form>' if refill else ''}
<p><small>Auto-refill authorizes automatic charges up to the monthly cap. Leave it off to approve every top-up yourself.</small></p></details>'''


def _balance_line(v: dict) -> str:
    b = v.get("balance_cents", 0)
    if b <= 0:
        return "Your balance is $0.00. Put money on when you are ready to put something online."
    days = int(b // max(1, v.get("tool_day_cents", 33)))
    return f"Your balance is {_money(b)}, about {days} days of one tool."


def _rate_line(v: dict) -> str:
    until = v.get("discount_until")
    if until and until > time.time():
        return f"A tool costs $10 a month, prorated by the day in each calendar month. Yours are half price, $5 a month, until {_date(until)}, thanks to your referral code."
    return "A tool costs $10 a month, prorated by the day in each calendar month."


def _referral_block(r: dict | None) -> str:
    if not r:
        return ""
    earned = f" So far: {_money(r['earned_cents'])} earned, {_money(r['unpaid_cents'])} not yet paid out." if r.get("earned_cents") else ""
    return f"""<div class=referral style="margin-top:22px;padding-top:18px;border-top:1px solid #E5EDF5"><p><strong>Earn 10% for every customer you bring in.</strong> Share your code <code>{_e(r['code'])}</code>, link, or QR. They get half-price app hosting for 60 days. You earn 10% of their paid hosting and storage usage for their lifetime. Monthly cash payouts start at $10; smaller balances roll over.{earned}</p><a class="btn s" href="/partners">Open partner dashboard</a></div>"""


def welcome(v: dict) -> str:
    a = f"/account/{_e(v['slug'])}"
    csrf = f'<input type=hidden name=csrf value="{_e(v["csrf"])}">'
    connect = (_connect(v["platform"], v["key"], v.get("code"), v["slug"], v.get("email", "")) if v.get("key") else
               f'<p>Get a fresh connection message, then paste it into your agent’s chat.</p><form method=post action="{a}/token">{csrf}<input type=hidden name=back value=welcome><input type=hidden name=name value=agent><button>Get a connection code</button></form>')
    cstat = ('Connected. Your agent is ready.' if v.get('connected') else 'Waiting for your agent to connect. This updates automatically.')
    tstat = (f'Your app is online: <a href="{_e(v["tools"][0].get("open", v["tools"][0]["url"]))}">{_e(v["tools"][0]["name"])}</a>' if v.get('tools') else '')
    funded = v.get('balance_cents', 0) > 0
    notice = f'<div class=notice>{_e(v["notice"])}</div>' if v.get('notice') else ''
    return page("Connect your agent — Boat House", f'''<div id=welcome class=onboarding data-ws="{_e(v['slug'])}">
<p class=flow-progress>Step 3 of 3 · Connect your agent</p><h1>Your account is ready.</h1><p class=lead>One paste connects the agent that built your app. It handles publishing and gives you a link to share.</p>{notice}
<div class=steps><div class="step {'done' if v.get('connected') else ''}" id=connect><div class=n>1</div><div><h3>Copy. Paste. You’re connected.</h3>{connect}<div class=status id=cstat role=status>{_e(cstat)}</div><div class=status id=tstat>{tstat}</div>
<details><summary>I use Claude or ChatGPT in my browser</summary><p>You need a coding agent that can access your app’s files, such as Claude Code, Codex, or Cursor. If you built the app in a regular chat, bring its files into one of those agents first. <a href="mailto:support@example.com">Email support if you’d like help.</a></p></details></div></div>
<div class="step {'done' if funded else ''}" id=card><div class=n>2</div><div><h3>{'Your hosting credit is ready' if funded else 'Add hosting credit when you’re ready'}</h3><p>{_balance_line(v)} A running app costs {_month_rate(v)}. Connecting your agent is free.</p>
<details class=billing-setup><summary>{'Manage hosting credit' if funded else 'Add hosting credit'}</summary>{_money_block(v, 'welcome')}</details></div></div>
<div class=step><div class=n>3</div><div><h3>Your agent takes it from here</h3><p>Keep working in the same conversation. Your agent can put the app online, share it with your team, and publish updates. You can say: <q>Put this app online with Boat House and give me the link.</q></p><div class=row><a class="btn s" href="/account?ws={_e(v['slug'])}">Go to my apps</a><a href="mailto:support@example.com">Get help</a></div></div></div></div></div>''', nav=f'<a href="/account?ws={_e(v["slug"])}">My apps</a><a href="/logout">Sign out</a>')


def once(title: str, heading: str, lead: str, value: str, note: str, slug: str) -> str:
    """One thing shown one time: an invite link to pass on."""
    return page(title, f"""<h1>{heading}</h1><p>{lead}</p><code class=once>{_e(value)}</code>{note}
<p><a class="btn s" href="/account?ws={_e(slug)}">Back to my apps</a></p>""",
                nav=f'<a href="/docs">Docs</a><a href="/account?ws={_e(slug)}">My apps</a><a href="/logout">Sign out</a>')


def access_request(v: dict) -> str:
    """The page behind the Allow button: who asks, for what, one button. Decided ones say so."""
    nav = f'<a href="/docs">Docs</a><a href="/account?ws={_e(v["slug"])}">My apps</a><a href="/logout">Sign out</a>'
    an = "an" if v["tier"][0] in "ae" else "a"
    if v.get("state") == "allowed":
        return page("Allowed", f"""<h1>Allowed. <span class=muted>{_e(v['email'])} is now {an} {_e(v['tier'])} on {_e(v['tool_name'])}.</span></h1>
<p class=lead>They got an email saying what to do next. You can take it back any time from your <a href="/account?ws={_e(v['slug'])}">workspace page</a>, or with <code>bh unshare {_e(v['tool'])} {_e(v['email'])}</code>. Close this tab.</p>""", nav=nav)
    if v.get("state") == "declined":
        return page("Not now", f"""<h1>Not now. <span class=muted>{_e(v['email'])} was not let in.</span></h1><p class=lead>Nothing changed. They can ask again. Close this tab.</p>""", nav=nav)
    if v.get("state") == "gone":
        return page("Already decided", f"""<h1>Already decided. <span class=muted>{_e(v.get('why', 'This request was already answered, or expired.'))}</span></h1><p class=lead>Nothing to do here.</p>""", nav=nav)
    what = {"viewer": "look at it", "editor": "change the data inside it", "admin": "change anything about it, including the software, the way you would edit a shared document"}[v["tier"]]
    msg = f'<p class=lead>They say: <em>“{_e(v["message"])}”</em></p>' if v.get("message") else ""
    csrf = f'<input type=hidden name=csrf value="{_e(v["csrf"])}"><input type=hidden name=t value="{_e(v["token"])}">'
    return page(f"{v['email']} asks to open {v['tool_name']}", f"""<h1>{_e(v['email'])} asks to be {an} {_e(v['tier'])} on {_e(v['tool_name'])}.</h1>
<p class=lead>That means they could {what}. Nothing is spent. You can take it back any time.</p>{msg}
<div class=row style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap">
<form class=inline method=post action="/requests/{_e(v['id'])}/allow">{csrf}<button>Allow</button></form>
<form class=inline method=post action="/requests/{_e(v['id'])}/decline">{csrf}<button class=s>Not now</button></form>
</div>
<p><small>Signed in as {_e(v['me'])}. Tool address: {_e(v['url'])}.</small></p>""", nav=nav)


def welcome_shared(v: dict) -> str:
    """Someone was shared in and just chose a password: open what was shared, connect an agent if they can change it, done."""
    tools = "".join(f'<li><span><a href="{_e(t["open"])}">{_e(t["name"])}</a><br><small>{_e(t["url"])} · you are {_e(t["tier"])}</small></span>'
                    f'<a class="btn s" href="{_e(t["open"])}">Open</a></li>' for t in v["tools"]) or "<li><small>Nothing is shared with you here yet.</small></li>"
    steps = [f"""<div class=step><div class=n>1</div><div><h3>Open what was shared with you</h3><p>You are signed in. These open straight away, no second password.</p><ul class=tools style="margin:12px 0 0">{tools}</ul></div></div>"""]
    if v.get("can_change"):
        connect = (_connect(v["platform"], v["key"], v.get("code"), v["slug"], v.get("email", "")) if v.get("key")
                   else f'<p>Get a connection code on your <a href="/account?ws={_e(v["slug"])}">workspace page</a> and paste it into your agent’s chat.</p>')
        example = f"{_e(v['slug'])}/{_e(v['tools'][0]['slug'])}" if v["tools"] else f"{_e(v['slug'])}/<tool>"
        steps.append(f"""<div class=step><div class=n>2</div><div><h3>Connect your agent, so it can make changes</h3><p>You are an admin here, so you can change the software itself, the way you would edit a shared document. Paste this into Claude Code, Cursor or Codex, then say what you want: “On {example}, change the page heading to ‘Welcome’ and put it live.”</p>{connect}</div></div>""")
    steps.append(f"""<div class=step><div class=n>{len(steps) + 1}</div><div><h3>Close this tab</h3><p>Come back any time at <a href="/account?ws={_e(v['slug'])}">{_e(v['platform'])}/account</a>; sign in with your email and the password you just chose.</p></div></div>""")
    return page(f"Welcome to {v['name']}", f"""<div id=welcome-shared>
<h1>You are in. <span class=muted>{_e(v['name'])} shared this with you.</span></h1>
<p class=lead>Signed in as {_e(v['email'])}. {'Two things' if v.get('can_change') else 'One thing'}, then close this tab.</p>
<div class=steps>{''.join(steps)}</div></div>""", nav=f'<a href="/docs">Docs</a><a href="/account?ws={_e(v["slug"])}">My apps</a><a href="/logout">Sign out</a>')


def new_key(key: str, name: str, email: str, platform: str, slug: str, code: str | None = None) -> str:
    return page("Connect your agent", f"""<h1>Connect your agent.</h1>
<p>Connect as {_e(email)} to {_e(slug)}. Your agent can do what you are allowed to do in your workspaces.</p>
{_connect(platform, key, code, slug, email)}
<p><a class="btn s" href="/account?ws={_e(slug)}">Back to my apps</a></p>""",
                nav=f'<a href="/docs">Help</a><a href="/account?ws={_e(slug)}">My apps</a><a href="/logout">Sign out</a>')


def _switcher(v: dict) -> str:
    items = []
    for m in v["memberships"]:
        label = f"{_e(m['name'])}"
        items.append(f"<strong>{label}</strong>" if m["slug"] == v["slug"] else f'<a href="/account?ws={_e(m["slug"])}">{label}</a>')
    return f"<div class=sw><span>Your teams:</span>{''.join(items)}</div>" if len(items) > 1 else ""


def account_member(v: dict) -> str:
    cards = "".join(f"<div class=card><h3>{_e(t['name'])}</h3><a class=url href=\"{_e(t.get('open', t['url']))}\">{_e(t['url'])}</a><div class=meta>your access: {_e(t['my_tier'] or 'none')}</div></div>"
                    for t in v["tools"]) or "<p><small>No tools shared with you yet.</small></p>"
    admin_on = [t["name"] for t in v["tools"] if t.get("my_tier") == "admin"]
    agent = ""
    if v.get("can_mint"):
        a = f"/account/{_e(v['slug'])}"
        why = (f"You are admin on {', '.join(_e(x) for x in admin_on)}, so your own agent can change the software, the way you would edit a shared document."
               if admin_on else "Your own agent can work in this workspace at the level you have been given.")
        agent = f"""<h2>Your agent</h2>
<p>{why} Get a connection code and paste it into your agent’s chat; from then on your <code>bh</code> knows this workspace too, and you can say things like “on {_e(v['slug'])}/{_e(v['tools'][0]['url'].split('//')[1].split('.')[0]) if v['tools'] else 'tool'}, change the About heading and put it live”.</p>
<form class=add method=post action="{a}/token"><input type=hidden name=csrf value="{_e(v['csrf'])}"><input type=hidden name=name value="agent"><button>Get a connection code</button></form>"""
    return page(v["name"], f"""<h1>{_e(v['name'])}</h1>
<p>Signed in as <code>{_e(v['email'])}</code>. You are a {_e(v['my_role'])} here: {'a member here sees every tool that is open to the workspace' if v['my_role'] == 'member' else 'a guest here sees only the tools shared with them'}. The balance and people belong to the owners.</p>
{_switcher(v)}
<h2>Your tools</h2><div class=cards>{cards}</div>{agent}""", nav='<a href="/docs">Docs</a><a href="/logout">Sign out</a>', wide=True)


def _rate_note(v: dict) -> str:
    until = v.get("discount_until")
    return f"<p><small>Half price on every tool until {_date(until)}, thanks to {_e(v.get('referred_by') or 'a referral code')}.</small></p>" if until and until > time.time() else ""


def _referred_table(r: dict | None) -> str:
    if not r or not r.get("referred"):
        return ""
    rows = "".join(f"<tr><td>{_e(x['name'])}</td><td>{_date(x['since'])}</td><td class=num>{_money(x['earned_cents'])}</td><td class=num>{_money(x['unpaid_cents'])}</td></tr>" for x in r["referred"])
    return f"<table><tr><th>Signed up with your code</th><th>Since</th><th class=num>Earned you</th><th class=num>Unpaid</th></tr>{rows}</table><p><small>Paid monthly in cash. We email you when a payout goes out.</small></p>"


def account(v: dict) -> str:
    a = f"/account/{_e(v['slug'])}"
    csrf = f'<input type=hidden name=csrf value="{_e(v["csrf"])}">'
    notice = f"<div class=notice>{_e(v['notice'])}</div>" if v.get("notice") else ""
    error = f"<div class='notice bad'>{_e(v['error'])}</div>" if v.get("error") else ""

    days = v["days_left"]
    left = f"{days} days left" if days is not None else "nothing burning"
    refill = (f"{_money(v['autorefill_cents'])} when under $5.00" + (f", at most {_money(v['autorefill_cap_cents'])} a month" if v["autorefill_cap_cents"] else "")) if v["autorefill_cents"] else "off"

    def tool_card(t):
        usage=t.get('usage'); capacity=''
        if usage:
            amount=usage.get('storage_bytes'); limit=usage['storage_limit_bytes']/1024**3
            measured=f"{amount/1024**3:.2f} of {limit:g} GB used" if amount is not None else f"{limit:g} GB storage limit"
            capacity=f'<div class=meta><strong>{measured}</strong><p>{_e(usage["message"])}</p></div>'
            if usage.get('sample_stale'): capacity+='<small>Usage is waiting for a fresh reading. Hard limits remain in effect.</small>'
            if limit<10:
                capacity+=f'''<details><summary>Increase storage</summary><form method=post action="/account/{_e(v['slug'])}/apps/{_e(t['slug'])}/capacity">{csrf}<label>Storage limit (GB)<input type=number name=storage_gb min="{int(limit)+1}" max=10 value="{int(limit)+1}" required></label><button class=s>Review price</button></form><small>No charge or capacity change until you approve the quote.</small></details>'''

        g = ", ".join(f"{_e(x['email'])} ({_e(x['tier'])})" for x in t["grants"]) or "owners only"
        rel = f"version {t['release']['seq']}" + (f" · {_e(t['release']['note'])}" if t["release"].get("note") else "") if t["release"] else "never deployed"
        who = "everyone in the workspace" if t["default_access"] == "members" else "only people listed"
        return (f"<div class=card><h3><span class='dot {'' if t['state'] == 'running' else 'off'}'></span>{_e(t['name'])}</h3>"
                f"<a class=url href=\"{_e(t.get('open', t['url']))}\">{_e(t['url'])}</a><div class=meta>{_e(t['state'])} · {rel}</div>"
                f"<div class=meta>{who} as {_e(t['default_tier'])}; shared with {g}</div>{capacity}</div>")
    tools = "".join(tool_card(t) for t in v["tools"]) or "<div class=card><h3>No tools yet</h3><div class=meta>Connect your agent, then say “put my tool online.” Your agent handles the setup and it appears here.</div></div>"

    keys = "".join(
        f"<tr><td>{_e(k['name'])}</td><td>{_e(k['email'])}</td><td>{'every workspace they belong to' if k.get('workspace_id') == '*' else 'this workspace'}</td><td>{_date(k['created'])}</td><td>{_ago(k['last_used'])}</td>"
        f"<td><form class=inline method=post action=\"{a}/keys/{_e(k['id'])}/revoke\">{csrf}<button class=s>Revoke</button></form></td></tr>"
        for k in v["keys"]) or "<tr><td colspan=5><small>No agent connections yet. Get a connection code above.</small></td></tr>"

    def person_row(p):
        actions = f'<form class=inline method=post action="{a}/invite/{_e(p["email"])}">{csrf}<button class=s>New invite link</button></form>'
        if p["role"] != "owner":
            actions += f' <form class=inline method=post action="{a}/people/{_e(p["email"])}/remove">{csrf}<button class=s>Remove</button></form>'
        return (f"<tr><td>{_e(p['email'])}</td><td>{_e(p['name'])}</td><td>{_e(p['role'])}</td>"
                f"<td>{'yes' if p['has_password'] else 'no, waiting on an invite'}</td><td>{_ago(p['last_login'])}</td><td>{actions}</td></tr>")
    people = "".join(person_row(p) for p in v["people"])

    doms = "".join(
        f"<tr><td><code>{_e(d['domain'])}</code></td><td>{'primary' if d['is_primary'] else 'attached'}</td><td>{_e(d['registrar'])}</td><td>{'pointing here' if d['dns_ok'] else 'DNS not pointing here yet'}</td></tr>"
        for d in v["domains"])
    doms += f"<tr><td><code>{_e(v['free_address'])}</code></td><td>free address</td><td>Boat House</td><td>always on</td></tr>"

    led = "".join(
        f"<tr><td>{_date(l['ts'])}</td><td>{_e(l['kind'])}</td><td class=num>{_money(l['amount_cents'])}</td><td class=num>{_money(l['balance_after'])}</td><td>{_e(l['memo'])}</td></tr>"
        for l in v["ledger"]) or "<tr><td colspan=5><small>No lines yet.</small></td></tr>"

    return page(f"{v['name']}", f"""<h1>{_e(v['name'])}</h1>
<div class=row style="justify-content:space-between">{_switcher(v)}<span><small>Signed in as {_e(v['email'])}</small></span></div>
{notice}{error}
<div class=strip>
<div><b>{_money(v['balance_cents'])}</b><span>balance{' · paused' if v['paused'] else ''}</span></div>
<div><b>{v['running']}</b><span>tool{'' if v['running'] == 1 else 's'} running</span></div>
<div><b>{_money(v['burn_cents'])}</b><span>a day · {left}</span></div>
<div><b>{'yes' if v['card_on_file'] else 'no'}</b><span>card on file · auto-refill {refill}</span></div>
</div>

<h2>Your apps</h2>
<div class=cards>{tools}</div>

<h2>Your agent</h2>
<p>Connect your agent so it can work here for you. Get a connection code, click Copy connection, and paste into your agent’s chat.</p>
<form class=add method=post action="{a}/token">{csrf}<input type=hidden name=name value="agent"><button>Get a connection code</button></form>
<details><summary>Manage connected agents</summary><table><tr><th>Name</th><th>For</th><th>Works in</th><th>Made</th><th>Last used</th><th></th></tr>{keys}</table></details>

<h2 id=billing>Hosting credit</h2>
{_money_block(v, "account")}
<div class=row style="margin-top:10px"><form class=inline method=post action="{a}/card">{csrf}<button class=s>{'Change the card' if v['card_on_file'] else 'Add a card'}</button></form></div>
<details><summary>Last ten lines of the ledger</summary><table><tr><th>When (UTC)</th><th>What</th><th class=num>Amount</th><th class=num>Balance</th><th>Memo</th></tr>{led}</table></details>
{_rate_note(v)}
<h2>Refer, earn 10%</h2>
{_referral_block(v.get('referral'))}
{_referred_table(v.get('referral'))}

<h2>People</h2>
<table><tr><th>Email</th><th>Name</th><th>Role</th><th>Password</th><th>Last sign-in</th><th></th></tr>{people}</table>
<form class=add method=post action="{a}/people">{csrf}
<label>Email<input type=email name=email required></label>
<label>Role<select name=role><option value=member>member</option><option value=guest>guest</option><option value=owner>owner</option></select></label>
<button>Add</button></form>
<p><small>Owners run the workspace. Members can open tools shared with everyone. Guests only see what is shared with them. Sharing a tool at a level is a sentence to your agent: “share the tracker with nancy@… as an editor”.</small></p>

<h2>Addresses</h2>
<table><tr><th>Domain</th><th></th><th>Registrar</th><th>DNS</th></tr>{doms}</table>
<p><small>A domain of your own is “buy us example.com” to your agent; it quotes the price and waits for your yes.</small></p>

<details><summary>Add a separate business or team</summary><p>Keep its apps, people, and billing separate.</p><form class=add method=post action="/account/new-workspace">{csrf}<label>App or business name<input name=workspace required minlength=3 maxlength=80 placeholder="e.g. Oak Street Studio"></label><button class=s>Add business or team</button></form></details>""",
                nav=f'<a href="https://{_e(v["base"])}/">Open the tools</a><a href="/docs">Docs</a><a href="/logout">Sign out</a>', wide=True)


# =============================================================================
# docs: the skill file, rendered for people (agents fetch /skill.md directly)
# =============================================================================

def _md(text: str) -> str:
    """Just enough markdown for SKILL.md: headings, fenced code, tables, lists, paragraphs, inline code/bold/links."""
    import html as _h
    lines = text.split("\n")
    if lines and lines[0].strip() == "---" and "---" in lines[1:]:
        lines = lines[lines.index("---", 1) + 1:]
    def inline(t: str) -> str:
        t = _h.escape(t, quote=False)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"\[([^\]]+)\]\(((?:https?://|mailto:|/)[^)\s]*)\)", r'<a href="\2">\1</a>', t)
        return t
    out, i = [], 0
    is_item = lambda l: re.match(r"^\s*([-*]|\d+\.)\s+", l)
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            j, buf = i + 1, []
            while j < len(lines) and not lines[j].startswith("```"):
                buf.append(lines[j]); j += 1
            out.append("<pre>" + _h.escape("\n".join(buf)) + "</pre>"); i = j + 1; continue
        m = re.match(r"^(#{1,3})\s+(.*)", l)
        if m:
            out.append(f"<h{len(m[1])}>{inline(m[2])}</h{len(m[1])}>"); i += 1; continue
        if l.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
            continue
        if is_item(l):
            ordered, items = bool(re.match(r"^\s*\d+\.", l)), []
            while i < len(lines) and (is_item(lines[i]) or (items and lines[i].startswith("   "))):
                if is_item(lines[i]):
                    items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>"); continue
        if not l.strip():
            i += 1; continue
        buf = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "```", "|")) and not is_item(lines[i]):
            buf.append(lines[i].strip()); i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")
    return "\n".join(out)


def legal(title: str, md: str, updated: str, platform: str = "boathousecloud.com") -> str:
    return page(f"Boat House {title}", f"""<h1>{_e(title)}</h1><p class=lead>Last changed {_e(updated)}. <a href="/terms">Terms of Service</a> · <a href="/privacy">Privacy Policy</a></p>
{_md(md)}""", nav='<a href="/">Home</a><a href="/docs">Docs</a><a href="/login">Sign in</a>',
                description=f"Boat House {title}: the terms and practices for using our hosted app service.",
                canonical=f"https://{platform}/" + ("privacy" if title == "Privacy Policy" else "terms"))


def docs(md: str, platform: str) -> str:
    md = re.sub(r"^(#{1,2}) ", r"#\1 ", md, flags=re.M)
    return page("Boat House Docs | Deploy, Share & Manage Apps", f"""<p><a href="/skill.md">This page as markdown, for agents</a></p>
<h1>Everything <code>bh</code> can do</h1>
<p class=lead>This page is written for your agent, which is why it talks about you in the third person. You are welcome to read along.</p>
<p>To connect, get a connection code on your <a href="/account">account page</a>, click <strong>Copy connection</strong>, and paste into your agent’s chat. Your agent handles installation and checks the connection. Python 3.9 or newer is required on the agent’s machine.</p>
{_md(md)}
<p><small>Agents: fetch <a href="/skill.md">/skill.md</a> (the same text as markdown) or <a href="/llms.txt">/llms.txt</a>.</small></p>""",
                nav='<a href="/#product">How it works</a><a href="/account">My apps</a><a class=btn href="/signup">Sign up</a>', wide=True,
                description="Connect your coding agent to Boat House. Learn how to deploy apps, share access, connect domains, and manage databases, backups, and exports.",
                canonical=f"https://{platform}/docs")
