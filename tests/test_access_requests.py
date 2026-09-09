"""Asking to be let in: the requester's agent asks, the owner presses one button, nobody types a share command."""
import os
import re
import sys
import tempfile
import time

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-req-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, mail, main  # noqa: E402

PLAT = "platform.test"
API = {"host": f"api.{PLAT}"}
P = {"host": PLAT}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver", follow_redirects=False) as cl:
        yield cl


@pytest.fixture(scope="module")
def world(client):
    """Nancy owns 'shared' with the tool db. Casey and Bob have their own workspaces and account keys."""
    main.create_workspace("Shared Req", "nancy-r@fixture.test"); main.create_workspace("Casey Req", "casey-r@fixture.test"); main.create_workspace("Bob Req", "bob-r@fixture.test")
    shared = db.workspace("shared-req")
    with db.conn() as c:
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_req", shared["id"], "db", "Global Color Meaning", "k", "pw", time.time(), "nancy-r@fixture.test"))
    for e in ("nancy-r@fixture.test", "casey-r@fixture.test", "bob-r@fixture.test"):
        auth.set_account_password(e, "correct horse battery")
    return {"shared-req": shared, "nancy": auth.create_project_key(shared["id"], "nancy-r@fixture.test", "n"),
            "casey": auth.create_project_key(auth.ACCOUNT, "casey-r@fixture.test", "me"), "bob": auth.create_project_key(auth.ACCOUNT, "bob-r@fixture.test", "me")}


@pytest.fixture
def mailbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html=None, **kw: sent.append({"to": to, "subject": subject, "text": text, "html": html, **kw}) or True)
    monkeypatch.setattr(mail, "configured", lambda: True)
    return sent


def test_the_owner_allows_with_one_button(world, client, mailbox):
    a = {**API, "Authorization": f"Bearer {world['casey']}"}
    r = client.post("/api/access-requests", headers=a, json={"workspace": "shared-req", "tool": "db", "tier": "admin", "message": "I will fix the About heading"})
    assert r.status_code == 200, r.text
    assert r.json()["emailed"] is True and r.json()["asked_count"] == 1 and r.json()["sent_to"] == 1
    assert "nancy-r@fixture.test" not in r.text          # who decides is not told to the person asking
    m = mailbox[-1]
    assert m["to"] == "nancy-r@fixture.test" and m["reply_to"] == "casey-r@fixture.test" and "asks to open" in m["subject"] and ">Allow<" in m["html"]
    link = re.search(r"https://platform\.test/requests/(rq_[a-f0-9]+)\?t=(req_[A-Za-z0-9_-]+)", m["text"])
    assert link, m["text"]
    path = link.group(0).replace(f"https://{PLAT}", "")
    # not signed in: the link sends you to sign in and comes back
    client.cookies.clear()
    r = client.get(path, headers=P)
    assert r.status_code == 302 and "/login?next=" in r.headers["location"] and "requests" in r.headers["location"]
    # a stranger who signs in cannot decide
    r = client.get(path, headers=P, cookies={"bh_account": auth.create_platform_session("bob-r@fixture.test")})
    assert r.status_code == 403
    # Nancy signs in and sees one button
    client.cookies.clear()
    nancy = {"bh_account": auth.create_platform_session("nancy-r@fixture.test")}
    r = client.get(path, headers=P, cookies=nancy)
    assert r.status_code == 200 and "asks to be an admin on Global Color Meaning" in r.text and ">Allow<" in r.text and "I will fix the About heading" in r.text
    csrf = re.search(r'name=csrf value="([^"]+)"', r.text)[1]
    rid, token = link.group(1), link.group(2)
    client.cookies.clear()
    r = client.post(f"/requests/{rid}/allow", headers=P, cookies=nancy, data={"csrf": csrf, "t": token})
    assert r.status_code == 200 and "Allowed." in r.text and "casey-r@fixture.test is now an admin" in r.text
    with db.conn() as c:
        g = c.execute("SELECT tier FROM grants WHERE tool_id='t_req' AND email='casey-r@fixture.test'").fetchone()
    assert g and g["tier"] == "admin"
    share = mailbox[-1]
    assert share["to"] == "casey-r@fixture.test" and "shared" in share["subject"] and "usual Boat House password" in share["text"]   # he has an account: no link
    # pressing it twice does nothing more
    client.cookies.clear()
    r = client.post(f"/requests/{rid}/allow", headers=P, cookies=nancy, data={"csrf": csrf, "t": token})
    assert r.status_code == 410 and "Already decided" in r.text
    # asking again for what you already have is a no-op
    r = client.post("/api/access-requests", headers=a, json={"workspace": "shared-req", "tool": "db", "tier": "editor"})
    assert r.json() == {"already": True, "tier": "admin", "tool": "db", "workspace": "shared-req"}


def test_owners_can_also_allow_from_their_agent(world, client, mailbox):
    b = {**API, "Authorization": f"Bearer {world['bob']}"}
    r = client.post("/api/access-requests", headers=b, json={"workspace": "shared-req", "tool": "db", "tier": "viewer"})
    assert r.status_code == 200 and not r.json().get("already")
    rid = r.json()["id"]
    assert r.json()["asked_count"] == 2          # Casey is an admin now, so he is asked too
    n = {**API, "Authorization": f"Bearer {world['nancy']}"}
    q = client.get("/api/access-requests", headers=n).json()["requests"]
    assert [x["email"] for x in q] == ["bob-r@fixture.test"] and q[0]["id"] == rid
    assert client.post(f"/api/access-requests/{rid}/allow", headers=b).status_code == 403     # Bob cannot let himself in
    r = client.post(f"/api/access-requests/{rid}/allow", headers=n)
    assert r.status_code == 200 and r.json()["decision"] == "allowed" and r.json()["tier"] == "viewer" and r.json()["emailed"] is True
    assert client.get("/api/access-requests", headers=n).json()["requests"] == []
    assert client.post(f"/api/access-requests/{rid}/allow", headers=n).status_code == 410


def test_unknown_tool_and_bad_tier(world, client, mailbox):
    a = {**API, "Authorization": f"Bearer {world['casey']}"}
    assert client.post("/api/access-requests", headers=a, json={"workspace": "shared-req", "tool": "nope", "tier": "admin"}).status_code == 404
    assert client.post("/api/access-requests", headers=a, json={"workspace": "shared-req", "tool": "db", "tier": "god"}).status_code == 422
