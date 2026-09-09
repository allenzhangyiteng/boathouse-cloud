"""One key is the person; sharing sends an email with three steps; joining lands on a welcome page."""
import os
import re
import sys
import tempfile
import time

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-acct-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, mail, main  # noqa: E402

PLAT = "platform.test"
API = {"host": f"api.{PLAT}"}
P = {"host": PLAT}
PW = "correct horse battery"


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver", follow_redirects=False) as cl:
        yield cl


@pytest.fixture(scope="module")
def two(client):
    """Nancy owns 'shared' with a tool; Casey owns 'starter-two'."""
    main.create_workspace("Shared", "nancy@fixture.test"); main.create_workspace("starter Two", "support@fixture.test")
    shared, starter = db.workspace("shared"), db.workspace("starter-two")
    with db.conn() as c:
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_db", shared["id"], "db", "Global Color Meaning", "k", "pw", time.time(), "nancy@fixture.test"))
    return {"shared": shared, "starter": starter, "nancy": auth.create_project_key(shared["id"], "nancy@fixture.test", "n"),
            "casey": auth.create_project_key(auth.ACCOUNT, "support@fixture.test", "me")}


def test_an_account_key_is_the_person_in_every_workspace(two, client):
    a = {**API, "Authorization": f"Bearer {two['casey']}"}
    me = client.get("/api/me", headers=a).json()
    assert me["account_key"] is True and [w["slug"] for w in me["workspaces"]] == ["starter-two"]
    assert client.get("/api/whoami", headers=a).json()["workspace"] == "starter-two"          # default: the first workspace
    assert client.get("/api/whoami", headers={**a, "X-Boathouse-Workspace": "shared"}).status_code == 403   # not shared yet
    # Nancy shares her tool with Casey as admin; the same key now works there too, at that level
    r = client.post("/api/tools/db/grants", headers={**API, "Authorization": f"Bearer {two['nancy']}"}, json={"email": "support@fixture.test", "tier": "admin"})
    assert r.status_code == 200
    me = client.get("/api/me", headers=a).json()
    assert [(w["slug"], w["role"]) for w in me["workspaces"]] == [("starter-two", "owner"), ("shared", "guest")]
    r = client.get("/api/whoami", headers={**a, "X-Boathouse-Workspace": "shared"})
    assert r.status_code == 200 and r.json()["role"] == "guest" and r.json()["balance_cents"] is None
    assert client.patch("/api/tools/db", headers={**a, "X-Boathouse-Workspace": "shared"}, json={"name": "GCM"}).status_code == 200
    assert client.get("/api/whoami", headers={**a, "X-Boathouse-Workspace": "nowhere"}).status_code == 404
    # a workspace-scoped key does not travel
    assert client.get("/api/whoami", headers={**API, "Authorization": f"Bearer {two['nancy']}", "X-Boathouse-Workspace": "starter-two"}).status_code == 403


def test_share_emails_three_steps_when_mail_is_set_up(two, client, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html=None, **kw: sent.append((to, subject, text)) or True)
    monkeypatch.setattr(mail, "configured", lambda: True)
    n = {**API, "Authorization": f"Bearer {two['nancy']}"}
    r = client.post("/api/tools/db/grants", headers=n, json={"email": "newperson@fixture.test", "tier": "admin"})
    assert r.status_code == 200 and r.json()["emailed"] is True and r.json()["invite_url"]
    to, subject, text = sent[-1]
    assert to == "newperson@fixture.test" and subject == 'nancy@fixture.test shared "GCM" with you'   # renamed in the test above
    assert r.json()["invite_url"] in text and "1. Open this link and choose a password" in text and "2. On the page that opens, click Copy connection" in text and "3. Tell your agent" in text and "shared/db" in text
    # a viewer gets simpler steps; someone with an account gets no link at all
    r = client.post("/api/tools/db/grants", headers=n, json={"email": "looker@fixture.test", "tier": "viewer"})
    assert "You land on the tool" in sent[-1][2] and "Paste it into your AI agent" not in sent[-1][2]
    auth.set_account_password("support@fixture.test", PW)          # someone who already has a password gets no link, just the address
    r = client.post("/api/tools/db/grants", headers=n, json={"email": "support@fixture.test", "tier": "editor"})
    assert r.json()["emailed"] is True and "usual Boat House password" in sent[-1][2] and "join/" not in sent[-1][2]


def test_without_mail_the_share_still_works_and_says_so(two, client, monkeypatch):
    monkeypatch.setattr(mail, "send", lambda *a, **kw: False)
    r = client.post("/api/tools/db/grants", headers={**API, "Authorization": f"Bearer {two['nancy']}"}, json={"email": "quiet@fixture.test", "tier": "viewer"})
    assert r.status_code == 200 and r.json()["emailed"] is False and r.json()["mail_configured"] is False and r.json()["invite_url"]


def test_joining_from_the_email_lands_on_a_welcome_page_with_the_line(two, client):
    with db.conn() as c:
        token = auth.create_invite(two["shared"]["id"], "newperson@fixture.test", "nancy@fixture.test")
    url = f"/join/{token}?verify={auth.invite_proof(token)}"
    r = client.get(url, headers=P)
    csrf = re.search(r'name=csrf value="([^"]+)"', r.text)[1]
    r = client.post(url, headers=P, data={"csrf": csrf, "name": "New", "password": PW, "password2": PW})
    assert r.status_code == 303 and r.headers["location"] == "/welcome?ws=shared&joined=1"
    sid = re.search(r"bh_account=([^;]+)", r.headers["set-cookie"])[1]
    wc = re.search(r"bh_welcome=([^;]+)", r.headers["set-cookie"])[1]
    page = client.get("/welcome?ws=shared&joined=1", headers=P, cookies={"bh_account": sid, "bh_welcome": wc}).text
    assert "You are in" in page and "Open what was shared" in page and "/open/" in page and "sh -s -- BH-" in page and "shared/db" in page
    code = re.search(r"sh -s -- (BH-[A-Z2-9-]{14})", page)[1]
    key = client.post("/api/claim", headers=API, json={"code": code}).json()["key"]
    me = client.get("/api/me", headers={**API, "Authorization": f"Bearer {key}"}).json()
    assert me["account_key"] is True and me["workspaces"][0]["slug"] == "shared"
    # the Open link signs them into the workspace host and lands on the tool, no second password
    r = client.get("/open/shared/db", headers=P, cookies={"bh_account": sid})     # minted at click time, 60 s, single use
    assert r.status_code == 303 and r.headers["location"].startswith(f"https://auth.shared.{PLAT}/handoff?t=")
    tok = re.search(r"handoff\?t=([^\"&]+)", r.headers["location"])[1]
    r = client.get(f"/handoff?t={tok}", headers={"host": f"auth.shared.{PLAT}"})
    assert r.status_code == 303 and r.headers["location"] == f"https://db.shared.{PLAT}" and "bh_session=" in r.headers["set-cookie"]
    assert client.get(f"/handoff?t={tok}", headers={"host": f"auth.shared.{PLAT}"}).status_code == 404   # spent


def test_mail_settings_are_host_owner_only_and_never_come_back(two, client):
    r = client.put("/api/mail", headers={**API, "Authorization": f"Bearer {two['nancy']}"}, json={"host": "smtp.x", "user": "u", "password": "p"})
    assert r.status_code == 403


@pytest.fixture(scope="module")
def host(client):
    """The host workspace's owner, the only person who may set up email."""
    from app import config
    if not db.workspace(config.HOST_WORKSPACE):
        main.create_workspace(config.HOST_WORKSPACE.title(), "owner@starter.test")
    ws = db.workspace(config.HOST_WORKSPACE)
    return {"h": {**API, "Authorization": f"Bearer {auth.create_project_key(ws['id'], 'owner@starter.test', 'ops')}"}}


def test_https_provider_sends_through_resend_and_says_why_when_it_cannot(host, client, monkeypatch):
    calls = []
    def fake(method, path, body=None, api_key=None):
        calls.append((method, path, body, api_key))
        if path == "/emails" and body["to"] == ["nobody@bounce.test"]:
            raise RuntimeError("Resend said: domain is not verified")
        return {"id": "em_1"}
    monkeypatch.setattr(mail, "_resend", fake)
    r = client.put("/api/mail", headers=host["h"], json={"provider": "resend", "api_key": "re_secret", "from": "support@platform.test"})
    assert r.status_code == 200 and r.json()["provider"] == "resend"
    st = client.get("/api/mail", headers=host["h"]).json()
    assert st["configured"] and st["provider"] == "resend" and st["from"] == "support@platform.test" and "re_secret" not in str(st)
    assert mail.send("someone@fixture.test", "hi", "there") is True
    m, p, body, _ = calls[-1]
    assert (m, p) == ("POST", "/emails") and body["to"] == ["someone@fixture.test"] and body["reply_to"] == "support@platform.test" and body["from"].endswith("<support@platform.test>")
    assert mail.send("nobody@bounce.test", "hi", "there") is False                       # sharing never breaks on a refusal
    r = client.post("/api/mail/test", headers=host["h"])
    assert r.status_code == 200 and r.json()["sent_to"] == "owner@starter.test"


def test_smtp_test_explains_blocked_ports(host, client, monkeypatch):
    client.put("/api/mail", headers=host["h"], json={"host": "smtp.gmail.com", "user": "a@b.test", "password": "p"})
    def boom(*a, **k):
        raise OSError(101, "Network is unreachable")
    monkeypatch.setattr(mail.smtplib, "SMTP", boom)
    r = client.post("/api/mail/test", headers=host["h"])
    assert r.status_code == 502 and "unreachable" in r.json()["detail"] and "--resend" in r.json()["detail"]


def test_mail_domain_writes_the_records_resend_wants(host, client, monkeypatch):
    from app import registrar
    client.put("/api/mail", headers=host["h"], json={"provider": "resend", "api_key": "re_secret", "from": "support@platform.test"})
    resend_calls = []
    def fake(method, path, body=None, api_key=None):
        resend_calls.append((method, path))
        if (method, path) == ("POST", "/domains"):
            return {"id": "dom_1", "status": "not_started", "records": [
                {"record": "SPF", "name": "send", "type": "MX", "value": "feedback-smtp.us-east-1.amazonses.com", "priority": 10},
                {"record": "SPF", "name": "send.platform.test", "type": "TXT", "value": "v=spf1 include:amazonses.com ~all"},
                {"record": "DKIM", "name": "resend._domainkey", "type": "TXT", "value": "p=MIGf"}]}
        if path.endswith("/verify"):
            return {}
        if path == "/domains/dom_1":
            return {"id": "dom_1", "status": "verified" if len([c for c in resend_calls if c[1].endswith('/verify')]) > 1 else "pending"}
        raise AssertionError(path)
    monkeypatch.setattr(mail, "_resend", fake)
    monkeypatch.setattr(registrar, "creds", lambda: {"apikey": "k", "secretapikey": "s"})
    written = []
    monkeypatch.setattr(registrar, "records", lambda d: [{"id": "old", "name": "send.platform.test", "type": "TXT", "content": "stale"}])
    monkeypatch.setattr(registrar, "_delete", lambda d, rid: written.append(("delete", rid)))
    monkeypatch.setattr(registrar, "create_record", lambda d, n, t, c, ttl=600, prio=None: written.append((d, n, t, c, prio)) or "new")
    r = client.post("/api/mail/domain", headers=host["h"])
    assert r.status_code == 200, r.text
    assert r.json()["domain"] == "platform.test" and r.json()["status"] == "pending"
    assert ("delete", "old") in written                                                   # stale TXT at send. replaced
    assert ("platform.test", "send", "MX", "feedback-smtp.us-east-1.amazonses.com", 10) in written
    assert ("platform.test", "send", "TXT", "v=spf1 include:amazonses.com ~all", None) in written
    assert ("platform.test", "resend._domainkey", "TXT", "p=MIGf", None) in written
    assert client.get("/api/mail/domain", headers=host["h"]).json()["status"] == "verified"
    r = client.get("/api/mail/domain", headers={**API, "Authorization": f"Bearer {auth.create_project_key(db.workspace('shared')['id'], 'nancy@fixture.test', 'n')}"})
    assert r.status_code == 403


def test_mail_domain_rerun_does_not_blink_records_that_are_already_right(host, client, monkeypatch):
    from app import registrar
    monkeypatch.setattr(mail, "_resend", lambda m, p, body=None, api_key=None: {"id": "dom_1", "status": "pending", "records": [
        {"record": "DKIM", "name": "resend._domainkey", "type": "TXT", "value": "p=MIGf"},
        {"record": "SPF", "name": "send", "type": "CNAME", "value": "send.forge.rmta.net"}]} if p in ("/domains", "/domains/dom_1") else {})
    monkeypatch.setattr(registrar, "creds", lambda: {"apikey": "k", "secretapikey": "s"})
    monkeypatch.setattr(registrar, "records", lambda d: [
        {"id": "r1", "name": "resend._domainkey.platform.test", "type": "TXT", "content": "p=MIGf"},
        {"id": "r2", "name": "send.platform.test", "type": "CNAME", "content": "send.forge.rmta.net."}])
    touched = []
    monkeypatch.setattr(registrar, "_delete", lambda d, rid: touched.append(("delete", rid)))
    monkeypatch.setattr(registrar, "create_record", lambda *a, **k: touched.append(("create", a)) or "x")
    r = client.post("/api/mail/domain", headers=host["h"])
    assert r.status_code == 200 and touched == [] and all(rec.get("unchanged") for rec in r.json()["records"])


def test_the_email_is_a_page_with_one_button_and_the_steps(two, client, monkeypatch):
    got = {}
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html=None, **kw: got.update(to=to, subject=subject, text=text, html=html) or True)
    mail.share_notice("p@fixture.test", "nancy@fixture.test", "GCM", "db", "https://db.shared.platform.test", "admin", "https://platform.test/join/abc", "shared")
    h = got["html"]
    assert h.startswith("<!doctype html>") and 'href="https://platform.test/join/abc"' in h and ">Open GCM<" in h
    assert h.count("border-radius:12px") == 3                                              # three numbered steps
    assert "shared/db" in h and "1. Open this link and choose a password" in got["text"] and "https://platform.test/join/abc" in got["text"]
    mail.share_notice("v@fixture.test", "nancy@fixture.test", "GCM", "db", "https://db.shared.platform.test", "viewer", None, "shared")
    assert 'href="https://db.shared.platform.test"' in got["html"] and "usual Boat House password" in got["html"]
