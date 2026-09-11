"""The human front door on the platform host: signup, sign-in, the account page."""
import os
import re
import sys
import tempfile

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-front-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, billing, db, main  # noqa: E402

PLAT = "platform.test"
P = {"host": PLAT}
PW = "correct horse battery"


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver", follow_redirects=False) as cl:
        yield cl


@pytest.fixture
def c(client):
    client.cookies.clear()          # each test starts signed out; cookies are passed explicitly
    return client


def csrf_of(html: str) -> str:
    return re.search(r'name=csrf value="([^"]+)"', html)[1]


def cookie_of(resp, name="bh_account") -> str:
    return re.search(name + r"=([^;]+)", resp.headers["set-cookie"])[1]


def test_landing_form_and_wrong_hosts(c):
    r = c.get("/", headers=P)          # the marketing site, with a way in
    assert r.status_code == 200 and "Boat House" in r.text and 'href="/signup"' in r.text
    r = c.get("/signup", headers=P)    # the sign-up form, under the name people expect
    assert r.status_code == 200 and "name=workspace" in r.text and 'href="/login"' in r.text
    r = c.get("/start", headers=P)     # the signup form
    assert r.status_code == 200 and "name=workspace" in r.text and "Create account" in r.text and 'href="/login"' in r.text
    assert c.get("/account", headers={"host": f"api.{PLAT}"}).status_code == 404
    assert c.post("/signup", headers={"host": f"auth.starter.{PLAT}"}, data={"x": "1"}).status_code == 404


def test_signup_creates_workspace_and_signs_in(c, monkeypatch):
    sent = []
    monkeypatch.setattr(main.mail, "send", lambda *args, **kwargs: sent.append(args) or True)
    csrf = csrf_of(c.get("/start", headers=P).text)
    r = c.post("/signup", headers=P, data={"csrf": csrf, "workspace": "Acme Studio", "email": "Owner@Acme.example", "password": PW})
    assert r.status_code == 202 and "Check your email" in r.text and not auth.has_password("owner@acme.example")
    url = re.search(r"https://\S+/verify-email/\S+", sent[0][2])[0]
    page = c.get(url, headers=P)
    r = c.post(url, headers=P, data={"csrf": csrf_of(page.text), "password": PW, "password2": PW})
    assert r.status_code == 303 and r.headers["location"] == "/welcome?ws=acme-studio"
    sc = r.headers["set-cookie"]
    assert "bh_account=" in sc and "Domain=" not in sc and "HttpOnly" in sc     # host-only on the apex
    assert auth.has_password("owner@acme.example")
    assert billing.balance(db.workspace("acme-studio")["id"]) == 0
    sid, wc = cookie_of(r), cookie_of(r, "bh_welcome")
    # the welcome page: the first key, shown from the short cookie, with the one line and the MCP config
    r = c.get("/welcome?ws=acme-studio", headers=P, cookies={"bh_account": sid, "bh_welcome": wc})
    assert r.status_code == 200 and "$0.00" in r.text and "install.sh | sh -s -- BH-" in r.text and "mcpServers" in r.text and "Waiting for your agent" in r.text
    code = re.search(r"sh -s -- (BH-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})", r.text)[1]
    assert "sh -s -- bh_" not in r.text                      # the pasted line never carries the key itself
    key = re.search(r"Bearer (bh_[A-Za-z0-9_-]{20,})", r.text)[1]   # the MCP config does, on purpose
    # the code trades for the key exactly once
    r = c.post("/api/claim", headers={"host": f"api.{PLAT}"}, json={"code": code.lower()})
    assert r.status_code == 200 and r.json()["key"] == key and r.json()["workspace"] == "acme-studio" and r.json()["api"] == f"https://api.{PLAT}"
    assert c.post("/api/claim", headers={"host": f"api.{PLAT}"}, json={"code": code}).status_code == 410
    assert c.post("/api/claim", headers={"host": f"api.{PLAT}"}, json={"code": "BH-NOPE-NOPE-NOPE"}).status_code == 410
    # without the cookie the key is gone for good; the page offers to mint another
    c.cookies.clear()
    r = c.get("/welcome?ws=acme-studio", headers=P, cookies={"bh_account": sid})
    assert r.status_code == 200 and key not in r.text and "Get a connection code" in r.text
    # the status feed flips once the key is used
    s = c.get("/welcome/status?ws=acme-studio", headers=P, cookies={"bh_account": sid}).json()
    assert {k: s[k] for k in ("connected", "connected_ago", "card_on_file", "balance_cents", "tools")} == {"connected": False, "connected_ago": "never", "card_on_file": False, "balance_cents": 0, "tools": []}
    assert c.get("/api/whoami", headers={"host": f"api.{PLAT}", "Authorization": f"Bearer {key}"}).status_code == 200
    s = c.get("/welcome/status?ws=acme-studio", headers=P, cookies={"bh_account": sid}).json()
    assert s["connected"] is True and s["connected_ago"] == "just now"
    r = c.get("/account?ws=acme-studio&welcome=1", headers=P, cookies={"bh_account": sid})
    assert r.status_code == 200 and "$0.00" in r.text and "Welcome" in r.text and "owner@acme.example" in r.text


def test_signup_short_password_and_taken_name(c):
    csrf = csrf_of(c.get("/start", headers=P).text)
    token = auth.signup_token("Other", "x@y.example", "")
    page = c.get(f"/verify-email/{token}", headers=P)
    r = c.post(f"/verify-email/{token}", headers=P, data={"csrf": csrf_of(page.text), "password": "short", "password2": "short"})
    assert r.status_code == 400 and "12 characters" in r.text
    r = c.post("/signup", headers=P, data={"csrf": csrf, "workspace": "Acme Studio", "email": "z@y.example", "password": PW})
    assert r.status_code == 409 and "taken" in r.text


def test_signup_existing_account_needs_its_password(c):
    csrf = csrf_of(c.get("/start", headers=P).text)
    r = c.post("/signup", headers=P, data={"csrf": csrf, "workspace": "Acme Two", "email": "owner@acme.example", "password": "not the password!"})
    assert r.status_code == 401 and "already has" in r.text
    r = c.post("/signup", headers=P, data={"csrf": csrf, "workspace": "Acme Two", "email": "owner@acme.example", "password": PW})
    assert r.status_code == 303 and r.headers["location"] == "/welcome?ws=acme-two"


def test_login_wrong_right_and_next(c):
    csrf = csrf_of(c.get("/login", headers=P).text)
    r = c.post("/login", headers=P, data={"csrf": csrf, "email": "owner@acme.example", "password": "wrong password!!", "next": "/account?ws=acme-studio"})
    assert r.status_code == 401
    r = c.post("/login", headers=P, data={"csrf": csrf, "email": "owner@acme.example", "password": PW, "next": "https://evil.com/"})
    assert r.status_code == 303 and r.headers["location"] == "/account"
    r = c.post("/login", headers=P, data={"csrf": csrf, "email": "owner@acme.example", "password": PW, "next": "/account?ws=acme-two"})
    assert r.headers["location"] == "/account?ws=acme-two"


def test_account_requires_session_and_old_address_redirects(c):
    r = c.get("/account", headers=P)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=")
    r = c.get("/account", headers={"host": f"acme-studio.{PLAT}"})
    assert r.status_code == 302 and r.headers["location"] == f"https://{PLAT}/account?ws=acme-studio"


def owner_session(c) -> str:
    return auth.create_platform_session("owner@acme.example")


def test_signup_page_when_already_signed_in_offers_workspaces(c):
    sid = owner_session(c)
    r = c.get("/signup", headers=P, cookies={"bh_account": sid})
    assert r.status_code == 200 and "already signed in" in r.text and "Acme Studio" in r.text and "Add a separate business or team" in r.text


def test_switcher_and_member_view(c):
    sid = owner_session(c)
    r = c.get("/account?ws=acme-two", headers=P, cookies={"bh_account": sid})
    assert r.status_code == 200 and "Acme Studio" in r.text and "Acme Two" in r.text
    # a member of acme-studio sees only the tools list there
    ws = db.workspace("acme-studio")
    c.post("/api/users", headers={"host": f"api.{PLAT}", "Authorization": f"Bearer {auth.create_project_key(ws['id'], 'owner@acme.example', 't')}"},
           json={"email": "member@acme.example", "role": "member"})
    auth.set_account_password("member@acme.example", PW)
    msid = auth.create_platform_session("member@acme.example")
    r = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": msid})
    assert r.status_code == 200 and "member here" in r.text and "Get a connection code" in r.text and "balance" in r.text and "$10.00" not in r.text
    # a member mints their own key from the page (they always could over the API); it acts as them, at their level
    r = c.post("/account/acme-studio/token", headers=P, cookies={"bh_account": msid}, data={"csrf": auth.csrf_token("account"), "name": "x"})
    assert r.status_code == 200 and "member@acme.example" in r.text


def test_mint_and_revoke_key(c):
    sid = owner_session(c)
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": sid}).text
    r = c.post("/account/acme-studio/token", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page), "name": "claude"})
    assert r.status_code == 200
    key = re.search(r"bh_[A-Za-z0-9_-]{20,}", r.text)[0]
    r = c.get("/api/whoami", headers={"host": f"api.{PLAT}", "Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["workspace"] == "acme-studio"
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": sid}).text
    kid = re.search(r"/account/acme-studio/keys/(pk_[0-9a-f]+)/revoke", page)[1]
    r = c.post(f"/account/acme-studio/keys/{kid}/revoke", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page)})
    assert r.status_code == 200 and "revoked" in r.text
    assert c.get("/api/whoami", headers={"host": f"api.{PLAT}", "Authorization": f"Bearer {key}"}).status_code == 401


def test_add_person_invite_and_join_on_platform(c, monkeypatch):
    sent = []
    monkeypatch.setattr(main.mail, "send", lambda *args, **kwargs: sent.append(args) or True)
    sid = owner_session(c)
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": sid}).text
    r = c.post("/account/acme-studio/people", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page), "email": "new@acme.example", "role": "guest"})
    assert r.status_code == 200
    link = re.search(rf"https://{PLAT}/join/(inv_[A-Za-z0-9_-]+)", r.text)
    assert link, "invite link should be on the platform host"
    token = link[1]
    r = c.get(f"/join/{token}", headers=P)
    assert r.status_code == 200 and "new@acme.example" in r.text and "name=password" not in r.text
    url = re.search(r"https://\S+/join/\S+", sent[0][2])[0]
    r = c.get(url, headers=P)
    r = c.post(url, headers=P, data={"csrf": csrf_of(r.text), "name": "New Person", "password": PW, "password2": PW})
    assert r.status_code == 303 and r.headers["location"] == "/welcome?ws=acme-studio&joined=1" and "bh_account=" in r.headers["set-cookie"]
    assert auth.has_password("new@acme.example")
    # the welcome page for someone shared in: what they can open (through a one-time handoff, no second password)
    nsid = cookie_of(r)
    page = c.get("/welcome?ws=acme-studio&joined=1", headers=P, cookies={"bh_account": nsid}).text
    assert "You are in" in page and "Close this tab" in page
    assert c.get("/handoff?t=bogus.token", headers={"host": f"auth.acme-studio.{PLAT}"}).status_code == 404
    # the same password now opens the tenant sign-in too
    r = c.get("/login", headers={"host": f"auth.acme-studio.{PLAT}"})
    r = c.post("/login", headers={"host": f"auth.acme-studio.{PLAT}"}, data={"csrf": csrf_of(r.text), "email": "new@acme.example", "password": PW, "next": ""})
    assert r.status_code == 303 and "bh_session=" in r.headers["set-cookie"]
    # An existing account gets its normal sign-in URL, never a password-reset credential.
    c.cookies.clear()                                   # the join left the new person's cookies in the jar
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": sid}).text
    r = c.post("/account/acme-studio/invite/new@acme.example", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page)})
    assert r.status_code == 200 and f"https://{PLAT}/login?next=" in r.text
    assert f"https://{PLAT}/join/" not in r.text
    r = c.post("/account/acme-studio/people/owner@acme.example/remove", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page)})
    assert r.status_code == 422 and "cannot remove yourself" in r.text
    r = c.post("/account/acme-studio/people/new@acme.example/remove", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page)})
    assert r.status_code == 200 and "removed" in r.text


def test_csrf_missing_refused_and_logout(c):
    sid = owner_session(c)
    r = c.post("/account/acme-studio/token", headers=P, cookies={"bh_account": sid}, data={"name": "x"})
    assert r.status_code == 400 and "expired" in r.text
    r = c.get("/logout", headers=P, cookies={"bh_account": sid})
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert c.get("/account", headers=P, cookies={"bh_account": sid}).status_code == 302


def test_card_without_stripe_shows_error(c):
    sid = owner_session(c)
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": sid}).text
    r = c.post("/account/acme-studio/card", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page)})
    assert r.status_code == 422 and "card processor" in r.text


def test_new_workspace_from_account(c):
    sid = owner_session(c)
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": sid}).text
    r = c.post("/account/new-workspace", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page), "workspace": "Third Thing"})
    assert r.status_code == 303 and "ws=third-thing" in r.headers["location"]
    assert db.workspace("third-thing") and auth.memberships("owner@acme.example")[0]["my_role"] == "owner"


def test_install_line_cli_skill_and_docs_are_served(c):
    r = c.get("/install.sh", headers=P)
    assert r.status_code == 200 and f'P="{PLAT}"' in r.text and '"https://$P/bh"' in r.text and 'login "https://api.$P"' in r.text and 'claim --api' in r.text
    r = c.get("/bh", headers=P)
    assert r.status_code == 200 and r.text.startswith("#!/usr/bin/env python3") and "def cmd_update" in r.text
    r = c.get("/skill.md", headers=P)
    assert r.status_code == 200 and "name: boathouse" in r.text and f"https://{PLAT}/install.sh" in r.text and "boathousecloud.com" not in r.text
    r = c.get("/docs", headers=P)
    assert r.status_code == 200 and "install.sh" in r.text and "<table>" in r.text and "<pre>" in r.text
    assert "bh login" in c.get("/llms.txt", headers=P).text
    for path in ("/install.sh", "/bh", "/skill.md", "/docs"):
        assert c.get(path, headers={"host": f"acme-studio.{PLAT}"}).status_code == 404


def test_keys_for_people_who_are_admin_on_a_tool(c):
    ws = db.workspace("acme-studio")
    api = {"host": f"api.{PLAT}", "Authorization": f"Bearer {auth.create_project_key(ws['id'], 'owner@acme.example', 't')}"}
    with db.conn() as k:
        k.execute("INSERT OR IGNORE INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_keys", ws["id"], "ledger", "Ledger", "k", "pw", 0, "owner@acme.example"))
    c.post("/api/users", headers=api, json={"email": "guest@acme.example", "role": "guest"})
    r = c.post("/api/keys", headers=api, json={"name": "g", "for": "guest@acme.example"})
    assert r.status_code == 422 and "bh share" in r.text
    with db.conn() as k:
        k.execute("INSERT INTO grants (id, tool_id, email, role, tier, created, created_by) VALUES (?,?,?,?,?,?,?)", ("g_keys", "t_keys", "guest@acme.example", "admin", "admin", 0, "owner@acme.example"))
    r = c.post("/api/keys", headers=api, json={"name": "g", "for": "guest@acme.example"})
    assert r.status_code == 200 and r.json()["email"] == "guest@acme.example"
    assert c.post("/api/keys", headers=api, json={"name": "x", "for": "nobody@acme.example"}).status_code == 422


def test_shared_in_admin_mints_their_own_key_and_plain_guest_cannot(c):
    ws = db.workspace("acme-studio")
    with db.conn() as k:
        k.execute("INSERT OR IGNORE INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_share", ws["id"], "site", "Site", "k", "pw", 0, "owner@acme.example"))
    api = {"host": f"api.{PLAT}", "Authorization": f"Bearer {auth.create_project_key(ws['id'], 'owner@acme.example', 't')}"}
    r = c.post("/api/tools/site/grants", headers=api, json={"email": "friend@else.example", "tier": "admin"})
    assert r.status_code == 200 and r.json()["workspace"] == "acme-studio" and r.json()["account_exists"] is False and r.json()["invite_url"]
    auth.set_account_password("friend@else.example", PW)
    sid = auth.create_platform_session("friend@else.example")
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": sid}).text
    assert "Your agent" in page and "admin on Site" in page and "Get a connection code" in page
    r = c.post("/account/acme-studio/token", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf_of(page), "name": "mine"})
    assert r.status_code == 200 and re.search(r"bh_[A-Za-z0-9_-]{20,}", r.text) and "sh -s -- BH-" in r.text
    # a viewer-only guest sees no key form and cannot mint
    c.post("/api/tools/site/grants", headers=api, json={"email": "looker@else.example", "tier": "viewer"})
    auth.set_account_password("looker@else.example", PW)
    lsid = auth.create_platform_session("looker@else.example")
    page = c.get("/account?ws=acme-studio", headers=P, cookies={"bh_account": lsid}).text
    assert "Get a connection code" not in page
    assert c.post("/account/acme-studio/token", headers=P, cookies={"bh_account": lsid}, data={"csrf": auth.csrf_token("account"), "name": "x"}).status_code == 403
