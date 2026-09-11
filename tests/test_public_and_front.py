"""Public tools, a domain whose front is a tool, sites without a Dockerfile, and money from the page."""
import io
import os
import sys
import tarfile
import tempfile
import time

os.environ.update(BH_METER="0", BH_DOMAIN="platform.test", BH_PUBLIC_IP="203.0.113.4", BH_PG_PASSWORD="x",
                  BH_OWNERS="owner@starter.test", BH_STATE_DIR=tempfile.mkdtemp(prefix="bh-pub-"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "control"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, deploy, hosts, main  # noqa: E402

PLAT = "platform.test"
API = {"host": f"api.{PLAT}"}


def tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            ti = tarfile.TarInfo(name); ti.size = len(body); tf.addfile(ti, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app, base_url="https://testserver", follow_redirects=False) as cl:
        yield cl


@pytest.fixture(scope="module")
def setup(client):
    main.create_workspace("Pub Co", "owner@pub.test")
    ws = db.workspace("pub-co")
    with db.conn() as c:
        c.execute("INSERT INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_site", ws["id"], "site", "Site", "sk", "pw", time.time(), "owner@pub.test"))
    return {"ws": ws, "key": auth.create_project_key(ws["id"], "owner@pub.test", "t")}


def authz(client, host, method="GET", cookies=None):
    return client.get("/internal/authz", headers={"host": host, "x-forwarded-uri": "/", "x-forwarded-method": method}, cookies=cookies or {})


def test_public_tool_reads_without_sign_in_and_still_needs_it_to_write(setup, client):
    h = f"site.pub-co.{PLAT}"
    assert authz(client, h).status_code == 302                      # private by default
    r = client.patch("/api/tools/site", headers={**API, "Authorization": f"Bearer {setup['key']}"}, json={"public": True})
    assert r.status_code == 200 and r.json()["public"] is True
    r = authz(client, h)
    from app import deploy
    tool = main._tool_by_slug(setup["ws"]["id"], "site")
    assert r.status_code == 200 and r.headers["x-boathouse-tier"] == "viewer" and r.headers["x-boathouse-user"] == "" and r.headers["x-boathouse-upstream"] == f"{deploy.ctr_name(tool)}:8080"
    assert authz(client, h, "POST").status_code == 302              # a write still asks for sign-in
    # a signed-in person with no share still reads it, as a viewer
    client.post("/api/users", headers={**API, "Authorization": f"Bearer {setup['key']}"}, json={"email": "guest@pub.test", "role": "guest"})
    sid = auth.create_session(setup["ws"]["id"], "guest@pub.test", "G")
    r = authz(client, h, cookies={"bh_session": sid})
    assert r.status_code == 200 and r.headers["x-boathouse-user"] == "guest@pub.test" and r.headers["x-boathouse-tier"] == "viewer"
    r = client.patch("/api/tools/site", headers={**API, "Authorization": f"Bearer {setup['key']}"}, json={"default_access": "members", "public": False})
    assert r.json()["public"] is False and authz(client, h).status_code == 302


def test_domain_front_is_the_tool_at_apex_and_www(setup, client):
    with db.conn() as c:
        c.execute("INSERT INTO domains (id, workspace_id, domain, registrar, status, is_primary, dns_ok, created, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                  ("d_1", setup["ws"]["id"], "pubco.example", "porkbun", "active", 1, 1, time.time(), "owner@pub.test"))
    assert hosts.resolve("pubco.example").kind == "home"
    assert hosts.resolve("www.pubco.example").slug == "www"        # a nonexistent tool called www, until the front is set
    h = {**API, "Authorization": f"Bearer {setup['key']}"}
    assert client.patch("/api/domains/pubco.example", headers=h, json={"tool": "nope"}).status_code == 404
    r = client.patch("/api/domains/pubco.example", headers=h, json={"tool": "site"})
    assert r.status_code == 200 and any(d["domain"] == "pubco.example" and d["tool"] == "site" for d in r.json()["domains"])
    for host in ("pubco.example", "www.pubco.example"):
        w = hosts.resolve(host)
        assert (w.kind, w.slug) == ("tool", "site")
    assert hosts.resolve("other.pubco.example").slug == "other" and hosts.resolve("auth.pubco.example").kind == "auth"
    assert hosts.tool_url(setup["ws"], "site") == "https://pubco.example"
    t = client.get("/api/tools/site", headers=h).json()
    assert t["url"] == "https://pubco.example" and "https://www.pubco.example" in t["urls"]
    client.patch("/api/domains/pubco.example", headers=h, json={"tool": ""})
    assert hosts.resolve("pubco.example").kind == "home"
    r = client.post("/api/tools", headers=h, json={"slug": "www", "name": "x"})
    assert r.status_code == 422 and "bh domain point" in r.text


def test_a_website_folder_gets_a_dockerfile_and_a_bare_folder_is_refused():
    out = deploy.ensure_dockerfile(tgz({"index.html": b"<h1>hi</h1>", "style.css": b"body{}"}))
    with tarfile.open(fileobj=io.BytesIO(out)) as tf:
        names = tf.getnames()
        assert set(names) == {"index.html", "style.css", "Dockerfile"}
        assert b"nginx" in tf.extractfile("Dockerfile").read() and b"listen 8080" in tf.extractfile("Dockerfile").read()
    same = tgz({"Dockerfile": b"FROM x", "app.py": b""})
    assert deploy.ensure_dockerfile(same) == same
    with pytest.raises(ValueError):
        deploy.ensure_dockerfile(tgz({"readme.md": b"x"}))


def test_money_from_the_page(setup, client):
    import re
    sid = auth.create_platform_session("owner@pub.test")
    P = {"host": PLAT}
    page = client.get("/welcome?ws=pub-co", headers=P, cookies={"bh_account": sid}).text
    assert "Start with $20" in page and "cannot charge your card" in page and "Add a payment method" in page
    assert "Saving your card charges nothing" in page and "Domains and extra storage cost extra" in page
    from app import pages
    connect = pages._connect("platform.test", "bh_x", "BH-AAAA-BBBB-CCCC")
    assert "Copy connection" in connect and "Do not change agent security settings" in connect
    csrf = re.search(r'name=csrf value="([^"]+)"', page)[1]
    r = client.post("/account/pub-co/topup", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf, "dollars": "20", "back": "welcome"})
    assert r.status_code == 422 and "Add a card first" in r.text
    with db.conn() as c:
        c.execute("UPDATE workspaces SET stripe_pm='pm_fake' WHERE id=?", (setup["ws"]["id"],))
    r = client.post("/account/pub-co/autorefill", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf, "dollars": "20", "cap": "100", "back": "welcome"})
    assert r.status_code == 303 and r.headers["location"] == "/welcome?ws=pub-co&refill=on"
    assert db.workspace("pub-co")["autorefill_cents"] == 2000 and db.workspace("pub-co")["autorefill_cap_cents"] == 10000
    page = client.get("/welcome?ws=pub-co&refill=on", headers=P, cookies={"bh_account": sid}).text
    assert "Auto-refill adds" in page and "Add $20 hosting credit" in page and "Turn auto-refill off" in page
    r = client.post("/account/pub-co/autorefill", headers=P, cookies={"bh_account": sid}, data={"csrf": csrf, "dollars": "0", "cap": "0"})
    assert r.headers["location"] == "/account?ws=pub-co&refill=off" and db.workspace("pub-co")["autorefill_cents"] == 0


def test_a_guest_key_works_on_the_shared_tool_and_nowhere_else(setup, client):
    h = {**API, "Authorization": f"Bearer {setup['key']}"}
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO tools (id, workspace_id, slug, name, signing_key, db_password, created, created_by) VALUES (?,?,?,?,?,?,?,?)",
                  ("t_other", setup["ws"]["id"], "other", "Other", "sk", "pw", time.time(), "owner@pub.test"))
    client.post("/api/tools/site/grants", headers=h, json={"email": "shared@else.example", "tier": "admin"})
    gkey = client.post("/api/keys", headers=h, json={"name": "g", "for": "shared@else.example"}).json()["key"]
    g = {**API, "Authorization": f"Bearer {gkey}"}
    me = client.get("/api/whoami", headers=g).json()
    assert me["email"] == "shared@else.example" and me["role"] == "guest" and me["workspace"] == "pub-co"
    assert [t["slug"] for t in client.get("/api/tools", headers=g).json()] == ["site"]      # only what was shared
    assert client.patch("/api/tools/site", headers=g, json={"name": "Site, renamed"}).status_code == 200   # admin on it
    assert client.patch("/api/tools/other", headers=g, json={"name": "x"}).status_code == 403             # not on this one
    assert client.post("/api/tools", headers=g, json={"slug": "mine", "name": "Mine"}).status_code == 403  # guests do not make tools
    assert client.get("/api/users", headers=g).status_code == 403                                         # owner things stay owner things


def test_terms_and_privacy_pages_exist_and_are_linked(client):
    P = {"host": PLAT}
    t = client.get("/terms", headers=P)
    assert t.status_code == 200 and "Terms of Service" in t.text and "$10 per running tool per month" in t.text and "Your card is charged only on your click" in t.text
    p = client.get("/privacy", headers=P)
    assert p.status_code == 200 and "Privacy Policy" in p.text and "Stripe" in p.text and "30 days" in p.text and "New York" in p.text
    s = client.get("/signup", headers=P).text
    assert 'href="/terms"' in s and 'href="/privacy"' in s


def test_demo_page_links_to_hosted_recording_without_bundling_account_media(client):
    r = client.get("/demo", headers={"host": PLAT})
    assert r.status_code == 200
    assert 'href="https://boathousecloud.com/demo"' in r.text
    assert 'href="/signup"' in r.text and 'href="/login"' in r.text
    assert "<video" not in r.text
    assert "<iframe" not in r.text

